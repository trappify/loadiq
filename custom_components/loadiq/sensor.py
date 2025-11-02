"""Sensor platform for LoadIQ."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .storage import LABEL_HEATPUMP
from .coordinator import LoadIQDataCoordinator, CoordinatorData
from .entity import LoadIQEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain_data = hass.data[DOMAIN]["entries"]
    data = domain_data[entry.entry_id]
    coordinator: LoadIQDataCoordinator = data[DATA_COORDINATOR]
    async_add_entities(
        [
            LoadIQLoadPowerSensor(coordinator, entry),
            LoadIQAverageRuntimeSensor(coordinator, entry),
            LoadIQRecentRunsSensor(coordinator, entry),
        ]
    )


class LoadIQLoadPowerSensor(LoadIQEntity, SensorEntity):
    """Represents the instantaneous inferred power draw of the target load."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LoadIQDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "load_power", "Load Power")

    @property
    def native_value(self) -> float | None:
        data: CoordinatorData | None = self.coordinator.data
        if not data:
            return None
        return round(data.current_power_w, 2)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        data: CoordinatorData | None = self.coordinator.data
        if not data:
            return None
        attrs: dict[str, object] = {
            "active": data.is_active,
            "confidence": data.active_confidence,
            "classification": "inactive",
        }
        segment = data.active_segment
        if segment:
            attrs.update(
                {
                    "active_since": segment.start.isoformat(),
                    "expected_stop": segment.end.isoformat(),
                    "segment_mean_w": segment.mean_power_w,
                    "segment_peak_w": segment.peak_power_w,
                    "classification": segment.classification,
                    "confidence_score": segment.confidence,
                }
            )
        else:
            attrs["confidence_score"] = 0.0
        return attrs


class LoadIQAverageRuntimeSensor(LoadIQEntity, SensorEntity):
    """Represents the average runtime of detected segments in minutes."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LoadIQDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "load_avg_runtime", "Average Runtime")

    @property
    def native_value(self) -> float | None:
        data: CoordinatorData | None = self.coordinator.data
        if not data:
            return None
        return round(data.avg_runtime_min, 2)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        data: CoordinatorData | None = self.coordinator.data
        if not data:
            return None
        return {
            "window_start": data.window_start.isoformat(),
            "window_end": data.window_end.isoformat(),
            "segments": len(data.segments),
            "heatpump_segments": sum(1 for seg in data.segments if seg.classification == LABEL_HEATPUMP),
        }


class LoadIQRecentRunsSensor(LoadIQEntity, SensorEntity):
    """Summarises detected runs in the recent history window."""

    _attr_icon = "mdi:timeline-clock"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LoadIQDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "recent_runs", "Recent Runs")

    @property
    def native_value(self) -> int | None:
        data: CoordinatorData | None = self.coordinator.data
        if not data:
            return None
        return len(self.coordinator.recent_segments(data))

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        data: CoordinatorData | None = self.coordinator.data
        if not data:
            return None
        segments = self.coordinator.recent_segments(data)
        runs: list[dict[str, object]] = []
        for seg in segments:
            runs.append(
                {
                    "start": seg.start.isoformat(),
                    "end": seg.end.isoformat(),
                    "duration_min": round(seg.duration.total_seconds() / 60, 1),
                    "energy_kwh": round(seg.energy_kwh, 3),
                    "mean_power_w": round(seg.mean_power_w, 0),
                    "peak_power_w": round(getattr(seg, "clamped_peak_w", seg.peak_power_w), 0),
                    "classification": seg.classification,
                    "confidence": round(seg.confidence, 3),
                }
            )
        return {
            "window_hours": self.coordinator.recent_runs_window.total_seconds() / 3600,
            "runs": runs,
        }
