from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.loadiq.const import (
    BACKEND_HOME_ASSISTANT,
    CONF_BACKEND,
    CONF_HOMEASSISTANT,
    CONF_HOUSE_SENSOR,
    CONF_KNOWN_LOADS,
    DOMAIN,
)
from loadiq.data.source import PowerDataSource


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("enable_custom_integrations"),
]

class StubSource(PowerDataSource):
    def __init__(self, now: pd.Timestamp, freq: str = "10s") -> None:
        self._now = now
        self._freq = freq

    def __enter__(self) -> "StubSource":
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def fetch_series(self, entity, start, end, aggregate=None):
        freq = aggregate or self._freq
        resolution = pd.Timedelta(freq)
        periods = 60
        end_ts = pd.Timestamp(self._now)
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")
        start_ts = end_ts - resolution * (periods - 1)
        index = pd.date_range(start=start_ts, periods=periods, freq=resolution)

        if entity.entity_id == "sensor.house":
            active_samples = 36  # 6 minutes above threshold
            values = [500.0] * (periods - active_samples) + [3200.0] * active_samples
        else:
            values = [0.0] * periods

        return pd.DataFrame({"value": values}, index=index)


async def test_entities_reflect_detection(monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant) -> None:
    now = pd.Timestamp(dt_util.utcnow())
    stub_source = StubSource(now)
    from loadiq.preprocessing.align import add_derived_columns, assemble_power_frame
    from loadiq.detection.segments import detect_heatpump_segments, DetectionConfig

    # Sanity-check stubbed data produces at least one detection segment
    sample_house = stub_source.fetch_series(type("obj", (), {"entity_id": "sensor.house"})(), None, None, aggregate="10s")
    frame = assemble_power_frame(sample_house, {}, None, freq="10s")
    frame = add_derived_columns(frame)
    segments = detect_heatpump_segments(frame, DetectionConfig())
    assert segments, "Stub data should yield at least one detection segment"
    monkeypatch.setattr(
        "loadiq.data.factory.create_power_data_source",
        lambda cfg, hass=None: stub_source,
    )
    monkeypatch.setattr(
        "custom_components.loadiq.coordinator.create_power_data_source",
        lambda cfg, hass=None: stub_source,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_HOME_ASSISTANT,
            CONF_HOMEASSISTANT: {
                CONF_HOUSE_SENSOR: "sensor.house",
                CONF_KNOWN_LOADS: [],
            },
        },
        title="LoadIQ (Home Assistant)",
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.loadiq.const import DATA_COORDINATOR

    assert entry.entry_id in hass.data[DOMAIN]
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    assert coordinator.data is not None
    assert coordinator.last_update_success
    await hass.async_block_till_done()

    from homeassistant.helpers import entity_registry as er

    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    expected_unique_ids = {
        f"{entry.entry_id}_load_power",
        f"{entry.entry_id}_load_avg_runtime",
        f"{entry.entry_id}_load_active",
    }
    assert {item.unique_id for item in entries} == expected_unique_ids

    lookup = {item.unique_id: item.entity_id for item in entries}
    power_entity_id = lookup[f"{entry.entry_id}_load_power"]
    runtime_entity_id = lookup[f"{entry.entry_id}_load_avg_runtime"]
    active_entity_id = lookup[f"{entry.entry_id}_load_active"]

    power_state = hass.states.get(power_entity_id)
    assert power_state and float(power_state.state) == pytest.approx(3200.0, rel=0.01)

    runtime_state = hass.states.get(runtime_entity_id)
    assert runtime_state and float(runtime_state.state) == pytest.approx(6.0, rel=0.1)

    active_state = hass.states.get(active_entity_id)
    assert active_state and active_state.state == "on"


async def test_homeassistant_history_source(monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant) -> None:
    from loadiq.data.homeassistant import HomeAssistantHistorySource
    from loadiq.config import EntityRef

    states = [
        State("sensor.house", "1000", last_changed=dt_util.utcnow() - timedelta(minutes=5)),
        State("sensor.house", "1200", last_changed=dt_util.utcnow() - timedelta(minutes=4)),
        State("sensor.house", "1400", last_changed=dt_util.utcnow() - timedelta(minutes=3)),
    ]

    def fake_history(hass_obj, start_time, end_time, entity_id, include_start_time_state=True, **kwargs):
        return {entity_id: states}

    monkeypatch.setattr(
        "homeassistant.components.recorder.history.state_changes_during_period",
        fake_history,
    )

    source = HomeAssistantHistorySource(hass)
    start = dt_util.utcnow() - timedelta(minutes=10)
    end = dt_util.utcnow()
    df = source.fetch_series(EntityRef(entity_id="sensor.house"), start, end, aggregate=None)
    assert not df.empty
    assert list(df["value"]) == [1000.0, 1200.0, 1400.0]
