from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.loadiq.const import (
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
    CONF_RECENT_RUNS_WINDOW_HOURS,
    CONF_OUTDOOR_SENSOR,
    DEFAULT_AGGREGATE_WINDOW,
    DEFAULT_RECENT_RUNS_WINDOW_HOURS,
    DOMAIN,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("enable_custom_integrations"),
]


async def test_config_flow_homeassistant(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.total_power", "4200", {})
    hass.states.async_set("sensor.heat_pump", "1800", {})

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BACKEND: BACKEND_HOME_ASSISTANT},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "homeassistant"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOUSE_SENSOR: "sensor.total_power",
            CONF_KNOWN_LOADS: ["sensor.heat_pump"],
            CONF_RECENT_RUNS_WINDOW_HOURS: 4,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_BACKEND] == BACKEND_HOME_ASSISTANT
    assert data[CONF_RECENT_RUNS_WINDOW_HOURS] == 4
    ha_cfg = data[CONF_HOMEASSISTANT]
    assert ha_cfg[CONF_HOUSE_SENSOR] == "sensor.total_power"
    assert ha_cfg[CONF_KNOWN_LOADS] == ["sensor.heat_pump"]


async def test_config_flow_influx(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.total_power", "4200", {})

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BACKEND: BACKEND_INFLUXDB},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "influx"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_INFLUX_URL: "http://localhost:8086",
            CONF_INFLUX_TOKEN: "token",
            CONF_INFLUX_ORG: "org",
            CONF_INFLUX_BUCKET: "bucket",
            CONF_INFLUX_VERIFY_SSL: True,
            CONF_INFLUX_TIMEOUT: 30,
            CONF_AGGREGATE_WINDOW: DEFAULT_AGGREGATE_WINDOW,
            CONF_HOUSE_SENSOR: "sensor.total_power",
            CONF_KNOWN_LOADS: [],
            CONF_RECENT_RUNS_WINDOW_HOURS: 6,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_BACKEND] == BACKEND_INFLUXDB
    assert data[CONF_RECENT_RUNS_WINDOW_HOURS] == 6
    influx_cfg = data[CONF_INFLUX]
    assert influx_cfg[CONF_INFLUX_URL] == "http://localhost:8086"
    assert influx_cfg[CONF_INFLUX_BUCKET] == "bucket"
    entities_cfg = data[CONF_ENTITIES]
    assert entities_cfg[CONF_HOUSE_SENSOR] == "sensor.total_power"
    assert entities_cfg[CONF_AGGREGATE_WINDOW] == DEFAULT_AGGREGATE_WINDOW


async def test_duplicate_flow_aborts(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_BACKEND: BACKEND_HOME_ASSISTANT, CONF_HOMEASSISTANT: {}},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_homeassistant(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.total_power", "4200", {})
    hass.states.async_set("sensor.heat_pump", "1800", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_HOME_ASSISTANT,
            CONF_HOMEASSISTANT: {
                CONF_HOUSE_SENSOR: "sensor.total_power",
                CONF_KNOWN_LOADS: [],
            },
            CONF_RECENT_RUNS_WINDOW_HOURS: DEFAULT_RECENT_RUNS_WINDOW_HOURS,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "homeassistant"

    with patch.object(
        hass.config_entries,
        "async_update_entry",
        wraps=hass.config_entries.async_update_entry,
    ) as mock_update:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_HOUSE_SENSOR: "sensor.total_power",
                CONF_KNOWN_LOADS: ["sensor.heat_pump"],
                CONF_RECENT_RUNS_WINDOW_HOURS: 5,
            },
        )
        await hass.async_block_till_done()
    option_payloads = [
        kwargs.get("options")
        for _, kwargs in mock_update.call_args_list
        if isinstance(kwargs.get("options"), dict)
    ]
    assert option_payloads
    new_options = next((payload for payload in option_payloads if payload), option_payloads[-1])
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    config = new_options or updated_entry.options or updated_entry.data
    assert config[CONF_HOMEASSISTANT][CONF_KNOWN_LOADS] == ["sensor.heat_pump"]
    assert config[CONF_RECENT_RUNS_WINDOW_HOURS] == 5


async def test_options_flow_influx(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.total_power", "4200", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_INFLUXDB,
            CONF_INFLUX: {
                CONF_INFLUX_URL: "http://localhost:8086",
                CONF_INFLUX_TOKEN: "token",
                CONF_INFLUX_ORG: "org",
                CONF_INFLUX_BUCKET: "bucket",
                CONF_INFLUX_VERIFY_SSL: True,
                CONF_INFLUX_TIMEOUT: 30,
            },
            CONF_ENTITIES: {
                CONF_HOUSE_SENSOR: "sensor.total_power",
                CONF_KNOWN_LOADS: [],
                CONF_AGGREGATE_WINDOW: DEFAULT_AGGREGATE_WINDOW,
            },
            CONF_RECENT_RUNS_WINDOW_HOURS: DEFAULT_RECENT_RUNS_WINDOW_HOURS,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "influx"

    with patch.object(
        hass.config_entries,
        "async_update_entry",
        wraps=hass.config_entries.async_update_entry,
    ) as mock_update:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_INFLUX_URL: "http://localhost:8086",
                CONF_INFLUX_TOKEN: "token-2",
                CONF_INFLUX_ORG: "org",
                CONF_INFLUX_BUCKET: "bucket",
                CONF_INFLUX_VERIFY_SSL: False,
                CONF_INFLUX_TIMEOUT: 45,
                CONF_AGGREGATE_WINDOW: "30s",
                CONF_HOUSE_SENSOR: "sensor.total_power",
                CONF_KNOWN_LOADS: [],
                CONF_RECENT_RUNS_WINDOW_HOURS: 8,
            },
        )
        await hass.async_block_till_done()
    option_payloads = [
        kwargs.get("options")
        for _, kwargs in mock_update.call_args_list
        if isinstance(kwargs.get("options"), dict)
    ]
    assert option_payloads
    new_options = next((payload for payload in option_payloads if payload), option_payloads[-1])
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    updated_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated_entry is not None
    config = new_options or updated_entry.options or updated_entry.data
    assert config[CONF_INFLUX][CONF_INFLUX_TOKEN] == "token-2"
    assert config[CONF_ENTITIES][CONF_AGGREGATE_WINDOW] == "30s"
    assert config[CONF_RECENT_RUNS_WINDOW_HOURS] == 8
