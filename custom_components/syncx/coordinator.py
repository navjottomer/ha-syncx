"""Polling coordinator for a single Luminous ConnectX plant."""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SyncXApiClient, SyncXAuthError, SyncXConnectionError
from .battery import SocEstimator, SocEstimatorConfig, SocResult, parse_curve
from .const import (
    ALL_FLOWS,
    ANIMATION_FLOW_MAP,
    BATTERY_HISTORY_SPAN,
    BATTERY_MATCH_TOLERANCE,
    CONF_BATTERY_POWER_ENTITY,
    CONF_INVERTER_EFFICIENCY,
    CONF_PLANT_ID,
    CONF_POWER_FACTOR,
    CONF_SCAN_INTERVAL,
    CONF_SOC_CELLS,
    CONF_SOC_CURVE,
    CONF_SOC_ENABLED,
    CONF_SOC_RESISTANCE,
    CONF_SOC_SMOOTHING,
    DEFAULT_INVERTER_EFFICIENCY,
    DEFAULT_POWER_FACTOR,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SOC_CELLS,
    DEFAULT_SOC_CURVE,
    DEFAULT_SOC_ENABLED,
    DEFAULT_SOC_RESISTANCE,
    DEFAULT_SOC_SMOOTHING,
    DETAIL_REFRESH_EVERY,
    DOMAIN,
    GRID_DEADBAND_W,
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


def _load_power_watts(data: SyncXData, power_factor: float) -> float | None:
    """Return the household load in watts.

    Built from the inverter's own primary measurements, output current and
    output voltage, rather than from the load figure the service publishes.
    That figure is these same two numbers multiplied by a fixed 0.8, so
    computing it here reproduces it exactly at the default power factor while
    letting a truer one be set.

    Falls back to the published figure on hardware that does not report the
    output current and voltage.
    """
    amps = to_float(data.stat("inverterCurrent"))
    volts = to_float(data.top("outputVoltage"))
    if amps is not None and volts is not None:
        return round(amps * volts * power_factor, 1)

    published = to_float(data.stat("consumptionValue"))
    return None if published is None else round(published * 1000.0, 1)


def _inverter_battery_watts(data: SyncXData) -> float | None:
    """Return battery power from the inverter's own current readings."""
    volts = to_float(data.top("batteryVoltage"))
    if volts is None:
        return None
    charging = to_float(data.stat("charging_current")) or 0.0
    discharging = to_float(data.stat("discharge")) or 0.0
    return round(volts * (charging - discharging), 1)


def _scan_interval(entry: ConfigEntry) -> timedelta:
    """Return the configured poll interval, clamped to a sane range."""
    minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        minutes = DEFAULT_SCAN_INTERVAL_MINUTES
    minutes = min(max(minutes, 1), 30)
    return timedelta(minutes=minutes)


def _grid_power_watts(data: SyncXData, efficiency: float = 1.0) -> float | None:
    """Return grid power in watts, positive importing and negative exporting.

    This comes from the inverter's own solar, load and battery figures rather
    than from its grid current transformer. The CT reading does not reconcile
    with the rest of the same sample: it runs 200 to 1200 W above the balance,
    and neither treating it as import nor as export produces the load the
    inverter itself reports. It most likely clamps the whole incoming mains
    rather than the inverter's grid port, which would also explain an air
    conditioner metering more energy than the inverter ever measured.

    The balance is self consistent by construction, so the flow always adds up:
    solar = home + battery + grid.

    ``solar_power`` is measured on the DC side -- it equals ``pvCurrent`` times
    ``solarVoltage`` exactly -- while the load is AC, so the two are not
    directly comparable. ``efficiency`` converts the solar figure to its AC
    equivalent before the subtraction; at 1.0 no correction is applied and
    export reads high by whatever the inverter loses in conversion.

    The load carries an approximation of its own: the service derives it as
    output current times output voltage times a fixed 0.8, so it assumes a
    power factor rather than measuring one. Nothing in the API can correct for
    that.

    The CT is still published as a raw current and voltage for anyone who
    wants it.
    """
    solar = to_float(data.stat("solar_power"))
    if solar is None or data.load_power is None:
        return None
    load = data.load_power / 1000.0

    if data.battery_power is None:
        return None
    battery_kw = data.battery_power / 1000.0

    # Positive surplus is energy the house and battery did not take.
    surplus_kw = solar * efficiency - load - battery_kw
    return round(-surplus_kw * 1000.0, 1)


def _grid_direction(power: float | None) -> str:
    """Return which way power is crossing the meter."""
    if power is None:
        return "unknown"
    if power > GRID_DEADBAND_W:
        return "import"
    if power < -GRID_DEADBAND_W:
        return "export"
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
    load_power: float | None = None
    battery_power: float | None = None
    battery_power_source: str = "inverter"
    grid_power: float | None = None
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
            update_interval=_scan_interval(entry),
            config_entry=entry,
        )
        self.client = client
        self.plant_id: str = entry.data[CONF_PLANT_ID]
        self._poll_count = 0
        self._details: dict[str, dict[str, Any]] = {}
        self._estimator: SocEstimator | None = None
        self._soc_enabled = True
        self._efficiency = DEFAULT_INVERTER_EFFICIENCY
        self._power_factor = DEFAULT_POWER_FACTOR
        self._battery_entity: str | None = None
        self._battery_history: deque[tuple[datetime, float]] = deque(maxlen=4000)
        self._unsub_battery: CALLBACK_TYPE | None = None
        self._configure_estimator()

    def _configure_estimator(self) -> None:
        """Build the state-of-charge estimator from the entry options."""
        options = self.config_entry.options
        self._efficiency = float(
            options.get(CONF_INVERTER_EFFICIENCY, DEFAULT_INVERTER_EFFICIENCY)
        )
        self._power_factor = float(options.get(CONF_POWER_FACTOR, DEFAULT_POWER_FACTOR))
        entity = options.get(CONF_BATTERY_POWER_ENTITY) or None
        if entity != self._battery_entity:
            self._battery_entity = entity
            self._battery_history.clear()
            self._watch_battery_entity()
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
        """Apply new settings after the options flow saved them."""
        self.update_interval = _scan_interval(self.config_entry)
        self._configure_estimator()
        await self.async_request_refresh()

    def _watch_battery_entity(self) -> None:
        """Record every reading of the external battery sensor as it arrives.

        The sensor updates continuously while the solar service only refreshes
        every few minutes, so its readings are buffered and matched against the
        inverter's own timestamp later rather than being read at poll time.
        """
        if self._unsub_battery is not None:
            self._unsub_battery()
            self._unsub_battery = None

        if not self._battery_entity:
            return

        @callback
        def _record(event) -> None:
            state = event.data.get("new_state")
            if state is None:
                return
            watts = to_float(state.state)
            if watts is None:
                return
            self._battery_history.append((state.last_updated, watts))
            cutoff = datetime.now(tz=UTC) - BATTERY_HISTORY_SPAN
            while self._battery_history and self._battery_history[0][0] < cutoff:
                self._battery_history.popleft()

        self._unsub_battery = async_track_state_change_event(
            self.hass, [self._battery_entity], _record
        )
        self.config_entry.async_on_unload(self._unsub_battery)

    def _aligned_battery_watts(self, reading_time: datetime | None) -> float | None:
        """Return the buffered battery reading closest to the given moment."""
        if reading_time is None or not self._battery_history:
            return None
        when, watts = min(self._battery_history, key=lambda s: abs(s[0] - reading_time))
        if abs(when - reading_time) > BATTERY_MATCH_TOLERANCE:
            return None
        return watts

    def _battery_watts(self, data: SyncXData) -> float | None:
        """Return battery power in watts, positive while charging.

        An external sensor is used when one is configured, because the
        inverter's own current reading sits at zero below a few amps. On the
        reference system it reported no current at all while the battery
        management system measured 9 A going in, which was enough to put the
        grid figure on the wrong side of zero.

        Falls back to the inverter's own reading when the external sensor is
        missing or unavailable, so losing it degrades accuracy rather than
        breaking the integration.
        """
        if self._battery_entity:
            aligned = self._aligned_battery_watts(data.last_updated)
            if aligned is not None:
                data.battery_power_source = "bms"
                return aligned
            _LOGGER.debug(
                "No %s reading within %s of the inverter timestamp %s, using the "
                "inverter's own figure instead",
                self._battery_entity,
                BATTERY_MATCH_TOLERANCE,
                data.last_updated,
            )

        data.battery_power_source = "inverter"
        return _inverter_battery_watts(data)

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

        # The battery term is resolved after last_updated is known, because the
        # external reading is matched against that timestamp.
        data.battery_power = self._battery_watts(data)

        active = ANIMATION_FLOW_MAP.get(str(stats.get("animationFlow") or ""), ())
        data.flows = {flow: flow in active for flow in ALL_FLOWS}
        data.load_power = _load_power_watts(data, self._power_factor)
        data.grid_power = _grid_power_watts(data, self._efficiency)
        data.grid_direction = _grid_direction(data.grid_power)

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
