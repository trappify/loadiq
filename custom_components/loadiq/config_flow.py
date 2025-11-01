from __future__ import annotations

from typing import Any, Dict

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    BACKEND_HOME_ASSISTANT,
    BACKEND_INFLUXDB,
    CONF_AGGREGATE_WINDOW,
    CONF_BACKEND,
    CONF_ENTITIES,
    CONF_HOMEASSISTANT,
    CONF_HOUSE_SENSOR,
    CONF_INFLUX,
    CONF_INFLUX_BUCKET,
    CONF_INFLUX_ORG,
    CONF_INFLUX_TIMEOUT,
    CONF_INFLUX_TOKEN,
    CONF_INFLUX_URL,
    CONF_INFLUX_VERIFY_SSL,
    CONF_KNOWN_LOADS,
    CONF_OUTDOOR_SENSOR,
    DEFAULT_AGGREGATE_WINDOW,
    DOMAIN,
    DOMAIN_TITLE,
)


class LoadIQConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for LoadIQ."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._backend: str | None = None

    async def async_step_user(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        """Select the data backend."""
        errors: dict[str, str] = {}
        if user_input is None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
        if user_input is not None:
            backend = user_input[CONF_BACKEND]
            self._backend = backend
            self._data[CONF_BACKEND] = backend
            if backend == BACKEND_HOME_ASSISTANT:
                return await self.async_step_homeassistant()
            return await self.async_step_influx()

        backend_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(
                        value=BACKEND_HOME_ASSISTANT,
                        label="Home Assistant sensors",
                    ),
                    selector.SelectOptionDict(
                        value=BACKEND_INFLUXDB,
                        label="InfluxDB",
                    ),
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_BACKEND, default=BACKEND_HOME_ASSISTANT): backend_selector,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_homeassistant(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        """Configure Home Assistant sensor sources."""
        errors: dict[str, str] = {}
        existing = self._data.get(CONF_HOMEASSISTANT, {})
        if user_input is not None:
            self._data[CONF_HOMEASSISTANT] = {
                CONF_HOUSE_SENSOR: user_input[CONF_HOUSE_SENSOR],
                CONF_OUTDOOR_SENSOR: user_input.get(CONF_OUTDOOR_SENSOR),
                CONF_KNOWN_LOADS: user_input.get(CONF_KNOWN_LOADS, []),
            }
            return self._create_entry()

        sensor_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )
        multi_sensor_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", multiple=True)
        )
        outdoor_default = existing.get(CONF_OUTDOOR_SENSOR)
        outdoor_field = (
            vol.Optional(CONF_OUTDOOR_SENSOR, default=outdoor_default)
            if outdoor_default
            else vol.Optional(CONF_OUTDOOR_SENSOR)
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOUSE_SENSOR,
                    default=existing.get(CONF_HOUSE_SENSOR),
                ): sensor_selector,
                outdoor_field: sensor_selector,
                vol.Optional(
                    CONF_KNOWN_LOADS,
                    default=existing.get(CONF_KNOWN_LOADS, []),
                ): multi_sensor_selector,
            }
        )
        return self.async_show_form(
            step_id="homeassistant",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_influx(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        """Configure direct InfluxDB access."""
        errors: dict[str, str] = {}
        existing_influx = self._data.get(CONF_INFLUX, {})
        existing_entities = self._data.get(CONF_ENTITIES, {})
        if user_input is not None:
            self._data[CONF_INFLUX] = {
                CONF_INFLUX_URL: user_input[CONF_INFLUX_URL],
                CONF_INFLUX_TOKEN: user_input[CONF_INFLUX_TOKEN],
                CONF_INFLUX_ORG: user_input[CONF_INFLUX_ORG],
                CONF_INFLUX_BUCKET: user_input[CONF_INFLUX_BUCKET],
                CONF_INFLUX_VERIFY_SSL: user_input.get(CONF_INFLUX_VERIFY_SSL, True),
                CONF_INFLUX_TIMEOUT: user_input.get(CONF_INFLUX_TIMEOUT),
            }
            self._data[CONF_ENTITIES] = {
                CONF_HOUSE_SENSOR: user_input[CONF_HOUSE_SENSOR],
                CONF_OUTDOOR_SENSOR: user_input.get(CONF_OUTDOOR_SENSOR),
                CONF_KNOWN_LOADS: user_input.get(CONF_KNOWN_LOADS, []),
                CONF_AGGREGATE_WINDOW: user_input.get(CONF_AGGREGATE_WINDOW, DEFAULT_AGGREGATE_WINDOW),
            }
            return self._create_entry()

        text_selector = selector.TextSelector()
        password_selector = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
        url_selector = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL))
        bool_selector = selector.BooleanSelector()
        number_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                step=1,
                unit_of_measurement="s",
                mode=selector.NumberSelectorMode.BOX,
            )
        )
        sensor_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )
        multi_sensor_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", multiple=True)
        )
        outdoor_default = existing_entities.get(CONF_OUTDOOR_SENSOR)
        outdoor_field = (
            vol.Optional(CONF_OUTDOOR_SENSOR, default=outdoor_default)
            if outdoor_default
            else vol.Optional(CONF_OUTDOOR_SENSOR)
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INFLUX_URL,
                    default=existing_influx.get(CONF_INFLUX_URL, "http://localhost:8086"),
                ): url_selector,
                vol.Required(
                    CONF_INFLUX_TOKEN,
                    default=existing_influx.get(CONF_INFLUX_TOKEN),
                ): password_selector,
                vol.Required(
                    CONF_INFLUX_ORG,
                    default=existing_influx.get(CONF_INFLUX_ORG),
                ): text_selector,
                vol.Required(
                    CONF_INFLUX_BUCKET,
                    default=existing_influx.get(CONF_INFLUX_BUCKET),
                ): text_selector,
                vol.Optional(
                    CONF_INFLUX_VERIFY_SSL,
                    default=existing_influx.get(CONF_INFLUX_VERIFY_SSL, True),
                ): bool_selector,
                vol.Optional(
                    CONF_INFLUX_TIMEOUT,
                    default=existing_influx.get(CONF_INFLUX_TIMEOUT, 30),
                ): number_selector,
                vol.Optional(
                    CONF_AGGREGATE_WINDOW,
                    default=existing_entities.get(CONF_AGGREGATE_WINDOW, DEFAULT_AGGREGATE_WINDOW),
                ): text_selector,
                vol.Required(
                    CONF_HOUSE_SENSOR,
                    default=existing_entities.get(CONF_HOUSE_SENSOR),
                ): sensor_selector,
                outdoor_field: sensor_selector,
                vol.Optional(
                    CONF_KNOWN_LOADS,
                    default=existing_entities.get(CONF_KNOWN_LOADS, []),
                ): multi_sensor_selector,
            }
        )
        return self.async_show_form(
            step_id="influx",
            data_schema=schema,
            errors=errors,
        )

    def _create_entry(self) -> FlowResult:
        title = DOMAIN_TITLE
        if self._backend == BACKEND_HOME_ASSISTANT:
            title = f"{DOMAIN_TITLE} (Home Assistant)"
        elif self._backend == BACKEND_INFLUXDB:
            title = f"{DOMAIN_TITLE} (InfluxDB)"
        return self.async_create_entry(title=title, data=self._data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "LoadIQOptionsFlowHandler":
        """Return the options flow handler."""
        return LoadIQOptionsFlowHandler(config_entry)


class LoadIQOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle LoadIQ options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        config = self._current_config()
        backend = config.get(CONF_BACKEND, BACKEND_HOME_ASSISTANT)
        if backend == BACKEND_HOME_ASSISTANT:
            return await self.async_step_homeassistant(user_input)
        return await self.async_step_influx(user_input)

    async def async_step_homeassistant(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        config = self._current_config()
        ha_config = config.get(CONF_HOMEASSISTANT, {})
        sensor_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )
        multi_sensor_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", multiple=True)
        )
        if user_input is not None:
            new_config = dict(config)
            new_config[CONF_HOMEASSISTANT] = {
                CONF_HOUSE_SENSOR: user_input[CONF_HOUSE_SENSOR],
                CONF_OUTDOOR_SENSOR: user_input.get(CONF_OUTDOOR_SENSOR),
                CONF_KNOWN_LOADS: user_input.get(CONF_KNOWN_LOADS, []),
            }
            self.hass.config_entries.async_update_entry(self._config_entry, options=new_config)
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        outdoor_default = ha_config.get(CONF_OUTDOOR_SENSOR)
        outdoor_field = (
            vol.Optional(CONF_OUTDOOR_SENSOR, default=outdoor_default)
            if outdoor_default
            else vol.Optional(CONF_OUTDOOR_SENSOR)
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOUSE_SENSOR, default=ha_config.get(CONF_HOUSE_SENSOR)): sensor_selector,
                outdoor_field: sensor_selector,
                vol.Optional(CONF_KNOWN_LOADS, default=ha_config.get(CONF_KNOWN_LOADS, [])): multi_sensor_selector,
            }
        )
        return self.async_show_form(step_id="homeassistant", data_schema=schema)

    async def async_step_influx(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        config = self._current_config()
        influx_config = config.get(CONF_INFLUX, {})
        entities = config.get(CONF_ENTITIES, {})
        text_selector = selector.TextSelector()
        password_selector = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))
        url_selector = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL))
        bool_selector = selector.BooleanSelector()
        number_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                step=1,
                unit_of_measurement="s",
                mode=selector.NumberSelectorMode.BOX,
            )
        )
        sensor_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        )
        multi_sensor_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", multiple=True)
        )
        if user_input is not None:
            new_config = dict(config)
            new_config[CONF_INFLUX] = {
                CONF_INFLUX_URL: user_input[CONF_INFLUX_URL],
                CONF_INFLUX_TOKEN: user_input[CONF_INFLUX_TOKEN],
                CONF_INFLUX_ORG: user_input[CONF_INFLUX_ORG],
                CONF_INFLUX_BUCKET: user_input[CONF_INFLUX_BUCKET],
                CONF_INFLUX_VERIFY_SSL: user_input.get(CONF_INFLUX_VERIFY_SSL, True),
                CONF_INFLUX_TIMEOUT: user_input.get(CONF_INFLUX_TIMEOUT),
            }
            new_config[CONF_ENTITIES] = {
                CONF_HOUSE_SENSOR: user_input[CONF_HOUSE_SENSOR],
                CONF_OUTDOOR_SENSOR: user_input.get(CONF_OUTDOOR_SENSOR),
                CONF_KNOWN_LOADS: user_input.get(CONF_KNOWN_LOADS, []),
                CONF_AGGREGATE_WINDOW: user_input.get(CONF_AGGREGATE_WINDOW, DEFAULT_AGGREGATE_WINDOW),
            }
            self.hass.config_entries.async_update_entry(self._config_entry, options=new_config)
            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        outdoor_default = entities.get(CONF_OUTDOOR_SENSOR)
        outdoor_field = (
            vol.Optional(CONF_OUTDOOR_SENSOR, default=outdoor_default)
            if outdoor_default
            else vol.Optional(CONF_OUTDOOR_SENSOR)
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_INFLUX_URL, default=influx_config.get(CONF_INFLUX_URL, "http://localhost:8086")): url_selector,
                vol.Required(CONF_INFLUX_TOKEN, default=influx_config.get(CONF_INFLUX_TOKEN)): password_selector,
                vol.Required(CONF_INFLUX_ORG, default=influx_config.get(CONF_INFLUX_ORG)): text_selector,
                vol.Required(CONF_INFLUX_BUCKET, default=influx_config.get(CONF_INFLUX_BUCKET)): text_selector,
                vol.Optional(CONF_INFLUX_VERIFY_SSL, default=influx_config.get(CONF_INFLUX_VERIFY_SSL, True)): bool_selector,
                vol.Optional(CONF_INFLUX_TIMEOUT, default=influx_config.get(CONF_INFLUX_TIMEOUT, 30)): number_selector,
                vol.Optional(CONF_AGGREGATE_WINDOW, default=entities.get(CONF_AGGREGATE_WINDOW, DEFAULT_AGGREGATE_WINDOW)): text_selector,
                vol.Required(CONF_HOUSE_SENSOR, default=entities.get(CONF_HOUSE_SENSOR)): sensor_selector,
                outdoor_field: sensor_selector,
                vol.Optional(CONF_KNOWN_LOADS, default=entities.get(CONF_KNOWN_LOADS, [])): multi_sensor_selector,
            }
        )
        return self.async_show_form(step_id="influx", data_schema=schema)

    def _current_config(self) -> Dict[str, Any]:
        if self._config_entry.options:
            return dict(self._config_entry.options)
        return dict(self._config_entry.data)
