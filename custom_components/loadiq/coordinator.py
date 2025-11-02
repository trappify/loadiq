from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any, Dict, List, Optional

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
    CONF_RECENT_RUNS_WINDOW_HOURS,
    DATA_CONFIG,
    DATA_STORAGE,
    DEFAULT_AGGREGATE_WINDOW,
    DEFAULT_RECENT_RUNS_WINDOW_HOURS,
    DOMAIN,
    DOMAIN_TITLE,
    LOOKBACK_WINDOW,
    UPDATE_INTERVAL,
)
from .storage import LoadIQStorage, LABEL_HEATPUMP, LABEL_OTHER

_LOGGER = logging.getLogger(__name__)


@dataclass
class CoordinatorData:
    """Detection snapshot exposed to entities."""

    segments: List[DetectedSegment]
    current_power_w: float
    is_active: bool
    avg_runtime_min: float
    active_segment: DetectedSegment | None
    active_confidence: str
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

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, storage: Optional[LoadIQStorage] = None) -> None:
        self.entry = entry
        self._storage = storage
        raw_config = entry.options or entry.data
        self._config = _build_runtime_config(raw_config)
        recent_hours = raw_config.get(CONF_RECENT_RUNS_WINDOW_HOURS, DEFAULT_RECENT_RUNS_WINDOW_HOURS)
        try:
            recent_hours_val = float(recent_hours)
        except (TypeError, ValueError):
            recent_hours_val = float(DEFAULT_RECENT_RUNS_WINDOW_HOURS)
        self._recent_runs_window = pd.Timedelta(hours=max(recent_hours_val, 1.0))
        min_duration = float(self._config.detection.min_duration_s)
        self._activation_confirm_s = max(90.0, min(180.0, min_duration / 2 if min_duration else 120.0))
        self._pending_active_since: pd.Timestamp | None = None
        self._latest_segments: List[DetectedSegment] = []
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
                _LOGGER.debug(
                    "No samples for house sensor %s between %s and %s",
                    self._config.entities.house_power.entity_id,
                    window_start,
                    window_end,
                )
                raise UpdateFailed("No samples retrieved for house power sensor")

            frame = assemble_power_frame(house, known, temp, freq=self._config.entities.house_power.aggregate_every)
            _LOGGER.debug(
                "LoadIQ frame stats: rows=%s net_last=%.1f min=%.1f max=%.1f",
                len(frame),
                float(frame["net_w"].iloc[-1]) if not frame.empty else float("nan"),
                float(frame["net_w"].min()) if not frame.empty else float("nan"),
                float(frame["net_w"].max()) if not frame.empty else float("nan"),
            )
            frame = add_derived_columns(
                frame,
                smoothing_window=self._config.detection.smoothing_window,
                baseline_window=self._config.detection.baseline_window,
            )
            segments = detect_heatpump_segments(frame, self._config.detection)
            for seg in segments:
                self._classify_segment(seg)
            if segments:
                last = segments[-1]
                _LOGGER.debug(
                    "LoadIQ detected %s segments (last: start=%s end=%s mean=%.1f peak=%.1f)",
                    len(segments),
                    last.start,
                    last.end,
                    last.mean_power_w,
                    last.peak_power_w,
                )
            current_power = float(frame["net_w"].iloc[-1]) if not frame.empty else 0.0
            now_ts = window_end

            sample_seconds = 0.0
            if "sample_interval_s" in frame.columns and not frame.empty:
                try:
                    sample_seconds = float(frame["sample_interval_s"].iloc[-1])
                except (TypeError, ValueError):
                    sample_seconds = 0.0
            aggregate_str = self._config.entities.house_power.aggregate_every
            try:
                aggregate_td = pd.to_timedelta(aggregate_str)
            except (ValueError, TypeError):
                aggregate_td = pd.Timedelta(seconds=max(sample_seconds, 1.0) if sample_seconds else 10.0)
            if not sample_seconds or sample_seconds <= 0:
                sample_seconds = max(aggregate_td.total_seconds(), 1.0)

            active_segment = next((seg for seg in segments if seg.start <= now_ts <= seg.end), None)
            if active_segment is not None:
                self._pending_active_since = None

            if active_segment is None and segments:
                last_segment = segments[-1]
                if now_ts - last_segment.end <= aggregate_td and current_power >= self._config.detection.min_power_w * 0.9:
                    active_segment = last_segment

            pending_segment = None
            if active_segment is None:
                pending_segment = self._build_pending_segment(frame, now_ts, sample_seconds)
                if pending_segment is not None:
                    self._classify_segment(pending_segment)
                    _LOGGER.debug(
                        "LoadIQ pending segment candidate: start=%s duration=%.1fs mean=%.1f peak=%.1f class=%s conf=%.2f",
                        pending_segment.start,
                        pending_segment.duration.total_seconds(),
                        pending_segment.mean_power_w,
                        pending_segment.peak_power_w,
                        pending_segment.classification,
                        pending_segment.confidence,
                    )

            has_positive = self._storage.has_positive_training() if self._storage else False

            confidence_str = "inactive"
            is_active = False

            def _fallback_active() -> bool:
                return current_power >= self._config.detection.min_power_w

            if active_segment is not None:
                confidence_str = self._format_confidence(active_segment)
                if active_segment.classification == LABEL_HEATPUMP:
                    is_active = True
                elif active_segment.classification in {"uncertain", "unknown"}:
                    is_active = (active_segment.confidence >= 0.5) or (not has_positive and _fallback_active())
                else:
                    is_active = False
            elif pending_segment is not None:
                confidence_str = self._format_confidence(pending_segment)
                if pending_segment.classification == LABEL_HEATPUMP and pending_segment.confidence >= 0.6:
                    is_active = True
                    active_segment = pending_segment
                elif not has_positive and _fallback_active():
                    is_active = True
                    active_segment = pending_segment
                else:
                    is_active = False
            else:
                if not has_positive and _fallback_active():
                    confidence_str = "heuristic"
                    is_active = True
                else:
                    confidence_str = "inactive"
                    is_active = False

            if pending_segment is not None and is_active and pending_segment not in segments:
                segments.append(pending_segment)

            avg_runtime_min = (
                sum(seg.duration.total_seconds() for seg in segments) / 60 / len(segments)
                if segments
                else 0.0
            )
            self._latest_segments = segments
            return CoordinatorData(
                segments=segments,
                current_power_w=current_power,
                is_active=is_active,
                avg_runtime_min=avg_runtime_min,
                active_segment=active_segment,
                active_confidence=confidence_str,
                window_start=window_start,
                window_end=window_end,
            )

        try:
            return await self.hass.async_add_executor_job(_compute)
        except UpdateFailed:
            raise
        except Exception as exc:  # pragma: no cover - best effort logging
            raise UpdateFailed(str(exc)) from exc

    def _classify_segment(self, segment: DetectedSegment) -> DetectedSegment:
        if self._storage is not None:
            classification, confidence = self._storage.classify_segment(segment)
        else:
            classification, confidence = ("unknown", 0.0)
        segment.classification = classification
        segment.confidence = confidence
        return segment

    @staticmethod
    def _format_confidence(segment: DetectedSegment) -> str:
        return f"{segment.classification}:{segment.confidence:.2f}"

    def _build_pending_segment(
        self,
        frame: pd.DataFrame,
        window_end: pd.Timestamp,
        sample_seconds: float,
    ) -> Optional[DetectedSegment]:
        detection = self._config.detection
        if frame.empty or sample_seconds <= 0.0:
            self._pending_active_since = None
            return None

        confirm_window = min(self._activation_confirm_s, detection.min_duration_s * 0.3)
        window_samples = max(1, int(round(confirm_window / sample_seconds)))
        recent = frame.iloc[-window_samples:]
        _LOGGER.debug(
            "LoadIQ pending window: samples=%s confirm_window=%.1fs net[min=%.1f max=%.1f]",
            len(recent),
            confirm_window,
            float(recent["net_w"].min()) if not recent.empty else float("nan"),
            float(recent["net_w"].max()) if not recent.empty else float("nan"),
        )
        if recent.empty:
            self._pending_active_since = None
            return None

        smoothed = recent["net_smoothed_w"]
        sustained = bool((smoothed >= detection.min_power_w * 0.9).all())

        start_rise = float(smoothed.max() - smoothed.min()) if not recent.empty else 0.0
        current_drop = float(smoothed.max() - smoothed.iloc[-1]) if not recent.empty else 0.0
        avg_power = float(smoothed.mean()) if not recent.empty else 0.0
        prev_pending = self._pending_active_since
        _LOGGER.debug(
            "LoadIQ pending stats: sustained=%s rise=%.1f drop=%.1f prev_pending=%s thresholds(min_power=%.0f start_delta=%.0f stop_delta=%.0f)",
            sustained,
            start_rise,
            current_drop,
            prev_pending,
            detection.min_power_w,
            detection.start_delta_w,
            detection.stop_delta_w,
        )

        if not sustained:
            self._pending_active_since = None
            return None

        if prev_pending is None and start_rise < detection.start_delta_w * 0.5:
            if avg_power < detection.min_power_w * 1.1:
                self._pending_active_since = None
                return None
            _LOGGER.debug(
                "LoadIQ promoting sustained high load without delta: avg_power=%.1f",
                avg_power,
            )

        if smoothed.iloc[-1] < detection.min_power_w * 0.7 and current_drop >= detection.stop_delta_w * 0.5:
            self._pending_active_since = None
            return None

        start_candidate = recent.index[0]
        if self._pending_active_since is None or start_candidate < self._pending_active_since:
            _LOGGER.debug(
                "LoadIQ pending start update: prev=%s candidate=%s",
                self._pending_active_since,
                start_candidate,
            )
            self._pending_active_since = start_candidate

        duration_s = max((window_end - self._pending_active_since).total_seconds(), 0.0)
        _LOGGER.debug(
            "LoadIQ pending duration check: duration=%.1fs threshold=%.1fs",
            duration_s,
            self._activation_confirm_s,
        )
        if duration_s < self._activation_confirm_s:
            return None

        energy_factor = sample_seconds / 3600.0
        recent_net = recent["net_w"]
        mean_power = float(recent_net.mean())
        peak_power = float(recent_net.max())
        energy_kwh = float(recent_net.sum() * energy_factor / 1000.0)

        baseline_series = recent.get("net_baseline_w")
        reference_power = float(baseline_series.iloc[0]) if baseline_series is not None else mean_power
        tolerance = max(detection.spike_tolerance_w, detection.spike_tolerance_ratio * reference_power)
        clamped_series = recent_net.clip(upper=reference_power + tolerance)
        clamped_energy_kwh = float(clamped_series.sum() * energy_factor / 1000.0)
        clamped_peak_w = float(clamped_series.max())

        temp_series = recent.get("outdoor_temp_c")
        if temp_series is not None:
            temp_mean = temp_series.mean()
            temperature_c = float(temp_mean) if pd.notna(temp_mean) else None
        else:
            temperature_c = None

        return DetectedSegment(
            start=self._pending_active_since,
            end=recent.index[-1],
            duration=pd.to_timedelta(duration_s, unit="s"),
            mean_power_w=mean_power,
            peak_power_w=peak_power,
            energy_kwh=energy_kwh,
            temperature_c=temperature_c,
            spike_energy_kwh=0.0,
            has_spike=False,
            clamped_energy_kwh=clamped_energy_kwh,
            clamped_peak_w=clamped_peak_w,
        )

    @property
    def recent_runs_window(self) -> pd.Timedelta:
        return self._recent_runs_window

    def recent_segments(self, data: CoordinatorData) -> List[DetectedSegment]:
        cutoff = data.window_end - self._recent_runs_window
        return [seg for seg in data.segments if seg.end >= cutoff]

    def find_segment_by_start(self, start: pd.Timestamp) -> Optional[DetectedSegment]:
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        else:
            start = start.tz_convert("UTC")
        data = self.data
        if not data:
            return None
        try:
            tolerance = pd.to_timedelta(self._config.entities.house_power.aggregate_every) * 2
        except (ValueError, TypeError):
            tolerance = pd.Timedelta(minutes=2)
        segments: List[DetectedSegment] = list(data.segments)
        if data.active_segment and data.active_segment not in segments:
            segments.append(data.active_segment)
        for seg in segments:
            delta = abs(seg.start - start)
            if delta <= tolerance:
                return seg
        return None
