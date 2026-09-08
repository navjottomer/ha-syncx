"""Polling coordinator for a single Luminous ConnectX plant."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SyncXApiClient, SyncXAuthError, SyncXConnectionError
from .battery import SocEstimator, SocEstimatorConfig, SocResult, parse_curve
from .const import (
    ALL_FLOWS,
    ANIMATION_FLOW_MAP,
    CONF_PLANT_ID,
    CONF_SOC_CELLS,
    CONF_SOC_CURVE,
    CONF_SOC_ENABLED,
    CONF_SOC_RESISTANCE,
    CONF_SOC_SMOOTHING,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SOC_CELLS,
    DEFAULT_SOC_CURVE,
    DEFAULT_SOC_ENABLED,
    DEFAULT_SOC_RESISTANCE,
    DEFAULT_SOC_SMOOTHING,
    DETAIL_REFRESH_EVERY,
    DOMAIN,
    FLOW_CENTER_TO_GRID,
    FLOW_GRID_TO_CENTER,
    GRID_DEADBAND_KW,
    STALE_AFTER,
)

_LOGGER = logging.getLogger(__name__)

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def to_float(value: Any) -> float | None:
    """Coerce an API value to a float, tolerating units and null strings.

    The service mixes bare numbers, numeric strings and strings with a unit
    appended such as ``"940.1 kWh"`` or ``"670.48 kg CO2e"``. It also spells a
    missing value as the literal text ``"null kWh"``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text or text.lower().startswith("null"):
        return None

    match = _NUMBER.search(text)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    """Coerce an API value to an int."""
    result = to_float(value)
    return None if result is None else int(result)


def duration_to_minutes(value: Any) -> float | None:
    """Convert an ``H:MM`` backup duration to minutes."""
    if not isinstance(value, str) or ":" not in value:
        return None
    hours_text, _, minutes_text = value.partition(":")
    try:
        return float(int(hours_text) * 60 + int(minutes_text))
    except ValueError:
        return None


def _grid_direction(data: SyncXData) -> str:
    """Return which way power is crossing the meter.

    The service reports grid current as an unsigned magnitude, so the direction
    has to come from somewhere else. The vendor flow code says so directly when
    it is one the dashboard recognises, but its table has real gaps -- codes
    such as 4.10 fall through to no flow at all -- and on those samples the
    direction has to be inferred instead.

    The fallback is a power balance: whatever solar produces that the house and
    the battery do not take has nowhere to go but out to the grid, and any
    shortfall has to come in from it.
    """
    if data.flows.get(FLOW_CENTER_TO_GRID):
        return "export"
    if data.flows.get(FLOW_GRID_TO_CENTER):
        return "import"

    solar = to_float(data.stat("solar_power"))
    load = to_float(data.stat("consumptionValue"))
    if solar is None or load is None:
        return "unknown"

    volts = to_float(data.top("batteryVoltage")) or 0.0
    charging = to_float(data.stat("charging_current")) or 0.0
    discharging = to_float(data.stat("discharge")) or 0.0
    battery_kw = volts * (charging - discharging) / 1000.0

    surplus = solar - load - battery_kw
    if surplus > GRID_DEADBAND_KW:
        return "export"
    if surplus < -GRID_DEADBAND_KW:
        return "import"
    return "idle"


def local_day_start(timezone_name: str | None) -> int:
    """Return the Unix timestamp of midnight today at the site.

    The trend endpoints reject an ISO date and expect the same local midnight
    epoch the web dashboard sends. The site record carries its own timezone, so
    that is preferred over the Home Assistant one.
    """
    tzinfo = UTC
    if timezone_name:
        try:
            tzinfo = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            tzinfo = UTC

    now = datetime.now(tz=tzinfo)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp())


@dataclass
class SyncXData:
    """Everything one poll produced, normalised for the entity layer."""

    stats: dict[str, Any] = field(default_factory=dict)
    plant: dict[str, Any] = field(default_factory=dict)
    battery: dict[str, Any] = field(default_factory=dict)
    inverter: dict[str, Any] = field(default_factory=dict)
    solar: dict[str, Any] = field(default_factory=dict)
    logger: dict[str, Any] = field(default_factory=dict)
    generation_trend: dict[str, Any] = field(default_factory=dict)
    consumption_trend: dict[str, Any] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    flows: dict[str, bool] = field(default_factory=dict)
    grid_direction: str = "unknown"
    soc: SocResult | None = None
    online: bool = False
    last_updated: datetime | None = None

    @property
    def inner(self) -> dict[str, Any]:
        """Return the nested live readings block of the stats payload."""
        inner = self.stats.get("stats")
        return inner if isinstance(inner, dict) else {}

    def stat(self, key: str) -> Any:
        """Return a value from the nested live readings block."""
        return self.inner.get(key)

    def top(self, key: str) -> Any:
        """Return a value from the top level of the stats payload."""
        return self.stats.get(key)


class SyncXCoordinator(DataUpdateCoordinator[SyncXData]):
    """Polls one plant on a fixed interval and derives its state."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SyncXApiClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.data.get(CONF_PLANT_ID)}",
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.plant_id: str = entry.data[CONF_PLANT_ID]
        self._poll_count = 0
        self._details: dict[str, dict[str, Any]] = {}
        self._estimator: SocEstimator | None = None
        self._soc_enabled = True
        self._configure_estimator()

    def _configure_estimator(self) -> None:
        """Build the state-of-charge estimator from the entry options."""
        options = self.config_entry.options
        self._soc_enabled = options.get(CONF_SOC_ENABLED, DEFAULT_SOC_ENABLED)
        if not self._soc_enabled:
            self._estimator = None
            return

        raw_curve = options.get(CONF_SOC_CURVE, DEFAULT_SOC_CURVE)
        try:
            curve = parse_curve(raw_curve)
        except ValueError:
            _LOGGER.warning(
                "Stored discharge curve is unusable, falling back to the default"
            )
            curve = parse_curve(DEFAULT_SOC_CURVE)

        config = SocEstimatorConfig(
            cells=int(options.get(CONF_SOC_CELLS, DEFAULT_SOC_CELLS)),
            resistance=float(options.get(CONF_SOC_RESISTANCE, DEFAULT_SOC_RESISTANCE)),
            smoothing=float(options.get(CONF_SOC_SMOOTHING, DEFAULT_SOC_SMOOTHING)),
            curve=curve,
        )

        if self._estimator is None:
            self._estimator = SocEstimator(config)
        else:
            self._estimator.reconfigure(config)

    async def async_options_updated(self) -> None:
        """Rebuild the estimator after the options flow saved new settings."""
        self._configure_estimator()
        await self.async_request_refresh()

    async def _optional(self, awaitable, label: str):
        """Await a supplementary fetch, logging and swallowing its failures.

        Authentication failures are re-raised so the caller can still open the
        reauth flow; only connection level problems are treated as skippable.
        """
        try:
            return await awaitable
        except SyncXAuthError:
            raise
        except SyncXConnectionError as err:
            _LOGGER.debug("Skipping %s this cycle: %s", label, err)
            return None

    async def _async_update_data(self) -> SyncXData:
        """Fetch one round of data for the configured plant."""
        try:
            stats = await self.client.async_get_stats(self.plant_id)

            # Site, battery bank and hardware records change rarely, so they are
            # only re-read every so often rather than on every five minute poll.
            if self._poll_count % DETAIL_REFRESH_EVERY == 0 or not self._details:
                self._details = {
                    "plant": await self.client.async_get_plant(self.plant_id),
                    "battery": await self.client.async_get_battery(self.plant_id),
                    "inverter": await self.client.async_get_inverter(self.plant_id),
                    "solar": await self.client.async_get_solar(self.plant_id),
                    "logger": await self.client.async_get_data_logger(self.plant_id),
                }
            day_start = local_day_start(self._details.get("plant", {}).get("timeZone"))

            # These three are supplementary. A failure in any of them should
            # leave the core readings intact rather than failing the poll.
            generation = await self._optional(
                self.client.async_get_generation_trend(self.plant_id, day_start),
                "generation trend",
            )
            consumption = await self._optional(
                self.client.async_get_consumption_trend(self.plant_id, day_start),
                "consumption trend",
            )
            alerts = await self._optional(
                self.client.async_get_alerts(self.plant_id), "alerts"
            )
        except SyncXAuthError as err:
            # Surfacing this as an auth failure makes Home Assistant open the
            # reauth flow rather than retrying bad credentials indefinitely.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SyncXConnectionError as err:
            raise UpdateFailed(str(err)) from err

        self._poll_count += 1

        data = SyncXData(
            stats=stats,
            plant=self._details.get("plant", {}),
            battery=self._details.get("battery", {}),
            inverter=self._details.get("inverter", {}),
            solar=self._details.get("solar", {}),
            logger=self._details.get("logger", {}),
            generation_trend=generation or {},
            consumption_trend=consumption or {},
            alerts=alerts or [],
        )

        inner = data.inner

        timestamp = to_int(inner.get("last_updated_timestamp"))
        if timestamp:
            data.last_updated = datetime.fromtimestamp(timestamp, tz=UTC)
            data.online = (datetime.now(tz=UTC) - data.last_updated) < STALE_AFTER

        active = ANIMATION_FLOW_MAP.get(str(stats.get("animationFlow") or ""), ())
        data.flows = {flow: flow in active for flow in ALL_FLOWS}
        data.grid_direction = _grid_direction(data)

        if self._estimator is not None and data.online:
            pack_volts = to_float(stats.get("batteryVoltage"))
            if pack_volts:
                data.soc = self._estimator.estimate(
                    pack_volts,
                    to_float(inner.get("charging_current")) or 0.0,
                    to_float(inner.get("discharge")) or 0.0,
                    time.time(),
                )

        return data
