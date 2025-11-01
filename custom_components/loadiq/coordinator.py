from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any, Dict, List

import pandas as pd
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from pydantic import SecretStr

from loadiq.config import (
    DataBackend,
    DetectionConfig,
    EntitiesConfig,
    EntityRef,
    InfluxConnection,
    KnownLoadConfig,
    LoadIQConfig,
)
from loadiq.data.factory import create_power_data_source
from loadiq.detection.segments import DetectedSegment, detect_heatpump_segments
from loadiq.preprocessing.align import add_derived_columns, assemble_power_frame

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
    DATA_CONFIG,
    DEFAULT_AGGREGATE_WINDOW,
    DOMAIN,
    DOMAIN_TITLE,
    LOOKBACK_WINDOW,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class CoordinatorData:
    """Detection snapshot exposed to entities."""

    segments: List[DetectedSegment]
    current_power_w: float
    is_active: bool
    avg_runtime_min: float
    active_segment: DetectedSegment | None
    window_start: pd.Timestamp
    window_end: pd.Timestamp


def _derive_name(entity_id: str) -> str:
    if "." in entity_id:
        return entity_id.split(".", 1)[1].replace("_", " ")
    return entity_id


def _build_entities_config(raw: Dict[str, Any], aggregate: str) -> EntitiesConfig:
    house = EntityRef(entity_id=raw[CONF_HOUSE_SENSOR], aggregate_every=aggregate)
    outdoor_entity = raw.get(CONF_OUTDOOR_SENSOR)
    outdoor = (
        EntityRef(entity_id=outdoor_entity, aggregate_every=aggregate)
        if outdoor_entity
        else None
    )
    known_load_ids = raw.get(CONF_KNOWN_LOADS, [])
    known = [
        KnownLoadConfig(
            name=_derive_name(entity_id),
            entity=EntityRef(entity_id=entity_id, aggregate_every=aggregate),
            subtract_from_house=True,
        )
        for entity_id in known_load_ids
    ]
    return EntitiesConfig(
        house_power=house,
        outdoor_temp=outdoor,
        known_loads=known,
    )


def _build_runtime_config(raw: Dict[str, Any]) -> LoadIQConfig:
    backend_value = raw.get(CONF_BACKEND, BACKEND_HOME_ASSISTANT)
    backend = DataBackend(backend_value)
    aggregate = DEFAULT_AGGREGATE_WINDOW
    influx_cfg: InfluxConnection | None = None
    entities_cfg: EntitiesConfig

    if backend == DataBackend.INFLUXDB:
        influx_raw = raw.get(CONF_INFLUX, {})
        entities_raw = raw.get(CONF_ENTITIES, {})
        aggregate = entities_raw.get(CONF_AGGREGATE_WINDOW, aggregate)
        entities_cfg = _build_entities_config(entities_raw, aggregate)
        influx_cfg = InfluxConnection(
            url=influx_raw[CONF_INFLUX_URL],
            token=SecretStr(influx_raw[CONF_INFLUX_TOKEN]),
            org=influx_raw[CONF_INFLUX_ORG],
            bucket=influx_raw[CONF_INFLUX_BUCKET],
            verify_ssl=influx_raw.get(CONF_INFLUX_VERIFY_SSL, True),
            timeout_s=float(influx_raw.get(CONF_INFLUX_TIMEOUT, 30)),
        )
    else:
        ha_raw = raw.get(CONF_HOMEASSISTANT, {})
        aggregate = ha_raw.get(CONF_AGGREGATE_WINDOW, aggregate)
        entities_cfg = _build_entities_config(ha_raw, aggregate)

    return LoadIQConfig(
        backend=backend,
        influx=influx_cfg,
        entities=entities_cfg,
        detection=DetectionConfig(),
    )


class LoadIQDataCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinates data refreshes for LoadIQ sensors."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        raw_config = entry.options or entry.data
        self._config = _build_runtime_config(raw_config)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN_TITLE,
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> CoordinatorData:
        now = dt_util.utcnow()
        window_end = pd.Timestamp(now)
        if window_end.tzinfo is None:
            window_end = window_end.tz_localize("UTC")
        else:
            window_end = window_end.tz_convert("UTC")
        window_start = window_end - pd.Timedelta(LOOKBACK_WINDOW)

        def _compute() -> CoordinatorData:
            with create_power_data_source(self._config, hass=self.hass) as source:
                house = source.fetch_series(
                    self._config.entities.house_power,
                    window_start.to_pydatetime(),
                    window_end.to_pydatetime(),
                    self._config.entities.house_power.aggregate_every,
                )
                known = {
                    load.name: source.fetch_series(
                        load.entity,
                        window_start.to_pydatetime(),
                        window_end.to_pydatetime(),
                        load.entity.aggregate_every,
                    )
                    for load in self._config.entities.known_loads
                }
                temp = (
                    source.fetch_series(
                        self._config.entities.outdoor_temp,
                        window_start.to_pydatetime(),
                        window_end.to_pydatetime(),
                        self._config.entities.outdoor_temp.aggregate_every,
                    )
                    if self._config.entities.outdoor_temp
                    else None
                )

            if house.empty:
                raise UpdateFailed("No samples retrieved for house power sensor")

            frame = assemble_power_frame(house, known, temp, freq=self._config.entities.house_power.aggregate_every)
            frame = add_derived_columns(
                frame,
                smoothing_window=self._config.detection.smoothing_window,
                baseline_window=self._config.detection.baseline_window,
            )
            segments = detect_heatpump_segments(frame, self._config.detection)
            current_power = float(frame["net_w"].iloc[-1]) if not frame.empty else 0.0
            now_ts = window_end
            active_segment = next((seg for seg in segments if seg.start <= now_ts <= seg.end), None)
            aggregate_td = pd.Timedelta(self._config.entities.house_power.aggregate_every)
            if active_segment is None and segments:
                last_segment = segments[-1]
                if now_ts - last_segment.end <= aggregate_td and current_power >= self._config.detection.min_power_w:
                    active_segment = last_segment
            is_active = bool(active_segment) or current_power >= self._config.detection.min_power_w
            avg_runtime_min = (
                sum(seg.duration.total_seconds() for seg in segments) / 60 / len(segments)
                if segments
                else 0.0
            )
            return CoordinatorData(
                segments=segments,
                current_power_w=current_power,
                is_active=is_active,
                avg_runtime_min=avg_runtime_min,
                active_segment=active_segment,
                window_start=window_start,
                window_end=window_end,
            )

        try:
            return await self.hass.async_add_executor_job(_compute)
        except UpdateFailed:
            raise
        except Exception as exc:  # pragma: no cover - best effort logging
            raise UpdateFailed(str(exc)) from exc
