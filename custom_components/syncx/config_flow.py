"""Config and options flow for Luminous ConnectX."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import SyncXApiClient, SyncXAuthError, SyncXConnectionError
from .battery import parse_curve
from .const import (
    CONF_BATTERY_POWER_ENTITY,
    CONF_INVERTER_EFFICIENCY,
    CONF_PLANT_ID,
    CONF_PLANT_NAME,
    CONF_POWER_FACTOR,
    CONF_SCAN_INTERVAL,
    CONF_SOC_CELLS,
    CONF_SOC_CURVE,
    CONF_SOC_ENABLED,
    CONF_SOC_RESISTANCE,
    CONF_SOC_SMOOTHING,
    CONF_USER_ID,
    DEFAULT_INVERTER_EFFICIENCY,
    DEFAULT_POWER_FACTOR,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SOC_CELLS,
    DEFAULT_SOC_CURVE,
    DEFAULT_SOC_ENABLED,
    DEFAULT_SOC_RESISTANCE,
    DEFAULT_SOC_SMOOTHING,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class SyncXConfigFlow(ConfigFlow, domain=DOMAIN):
    """Walk the user through signing in and picking a plant."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._email: str = ""
        self._password: str = ""
        self._user_id: str = ""
        self._plants: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials and look up the account's plants."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            self._password = user_input[CONF_PASSWORD]

            client = SyncXApiClient(
                async_get_clientsession(self.hass), self._email, self._password
            )
            try:
                await client.async_login()
                self._plants = await client.async_get_plants()
            except SyncXAuthError:
                errors["base"] = "invalid_auth"
            except SyncXConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error while signing in")
                errors["base"] = "unknown"
            else:
                self._user_id = client.user_id or ""
                if not self._plants:
                    errors["base"] = "no_plants"
                elif len(self._plants) == 1:
                    return await self._async_create(self._plants[0])
                else:
                    return await self.async_step_plant()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_plant(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which plant to add when several exist."""
        if user_input is not None:
            chosen = next(
                (p for p in self._plants if p.get("id") == user_input[CONF_PLANT_ID]),
                None,
            )
            if chosen is not None:
                return await self._async_create(chosen)

        options = {
            plant["id"]: plant.get("plantName") or plant["id"]
            for plant in self._plants
            if plant.get("id")
        }
        return self.async_show_form(
            step_id="plant",
            data_schema=vol.Schema({vol.Required(CONF_PLANT_ID): vol.In(options)}),
        )

    async def _async_create(self, plant: dict[str, Any]) -> ConfigFlowResult:
        """Create the config entry for one plant."""
        plant_id = plant["id"]
        await self.async_set_unique_id(plant_id)
        self._abort_if_unique_id_configured()

        name = plant.get("plantName") or "Luminous ConnectX"
        return self.async_create_entry(
            title=name,
            data={
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
                CONF_PLANT_ID: plant_id,
                CONF_PLANT_NAME: name,
                CONF_USER_ID: self._user_id,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the stored password stopped working."""
        self._email = entry_data.get(CONF_EMAIL, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh password and verify it before storing."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            client = SyncXApiClient(
                async_get_clientsession(self.hass), self._email, password
            )
            try:
                await client.async_login()
            except SyncXAuthError:
                errors["base"] = "invalid_auth"
            except SyncXConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    )
                }
            ),
            description_placeholders={"email": self._email},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SyncXOptionsFlow:
        """Return the options flow handler."""
        return SyncXOptionsFlow()


class SyncXOptionsFlow(OptionsFlow):
    """Tune the battery state-of-charge estimation."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and validate the estimator settings."""
        errors: dict[str, str] = {}
        options = self.config_entry.options

        if user_input is not None:
            try:
                parse_curve(user_input[CONF_SOC_CURVE])
            except ValueError:
                errors[CONF_SOC_CURVE] = "invalid_curve"
            else:
                return self.async_create_entry(
                    data={
                        CONF_INVERTER_EFFICIENCY: float(
                            user_input[CONF_INVERTER_EFFICIENCY]
                        ),
                        CONF_POWER_FACTOR: float(user_input[CONF_POWER_FACTOR]),
                        CONF_SCAN_INTERVAL: float(user_input[CONF_SCAN_INTERVAL]),
                        CONF_BATTERY_POWER_ENTITY: user_input.get(
                            CONF_BATTERY_POWER_ENTITY
                        ),
                        CONF_SOC_ENABLED: user_input[CONF_SOC_ENABLED],
                        CONF_SOC_CELLS: int(user_input[CONF_SOC_CELLS]),
                        CONF_SOC_RESISTANCE: float(user_input[CONF_SOC_RESISTANCE]),
                        CONF_SOC_SMOOTHING: float(user_input[CONF_SOC_SMOOTHING]),
                        CONF_SOC_CURVE: user_input[CONF_SOC_CURVE].strip(),
                    }
                )

        current = user_input or options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INVERTER_EFFICIENCY,
                    default=current.get(
                        CONF_INVERTER_EFFICIENCY, DEFAULT_INVERTER_EFFICIENCY
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.8, max=1.0, step=0.01, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MINUTES,
                        max=MAX_SCAN_INTERVAL_MINUTES,
                        step=0.5,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(
                    CONF_BATTERY_POWER_ENTITY,
                    description={
                        "suggested_value": current.get(CONF_BATTERY_POWER_ENTITY)
                    },
                ): EntitySelector(
                    EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required(
                    CONF_POWER_FACTOR,
                    default=current.get(CONF_POWER_FACTOR, DEFAULT_POWER_FACTOR),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.5, max=1.0, step=0.01, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_SOC_ENABLED,
                    default=current.get(CONF_SOC_ENABLED, DEFAULT_SOC_ENABLED),
                ): bool,
                vol.Required(
                    CONF_SOC_CELLS,
                    default=current.get(CONF_SOC_CELLS, DEFAULT_SOC_CELLS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=64, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_SOC_RESISTANCE,
                    default=current.get(CONF_SOC_RESISTANCE, DEFAULT_SOC_RESISTANCE),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=1,
                        step=0.001,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="Ω",
                    )
                ),
                vol.Required(
                    CONF_SOC_SMOOTHING,
                    default=current.get(CONF_SOC_SMOOTHING, DEFAULT_SOC_SMOOTHING),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.01, max=1, step=0.01, mode=NumberSelectorMode.SLIDER
                    )
                ),
                vol.Required(
                    CONF_SOC_CURVE,
                    default=current.get(CONF_SOC_CURVE, DEFAULT_SOC_CURVE),
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
