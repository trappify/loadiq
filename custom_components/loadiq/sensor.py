"""Sensor platform for LoadIQ."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import LoadIQDataCoordinator, CoordinatorData
from .entity import LoadIQEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: LoadIQDataCoordinator = data[DATA_COORDINATOR]
    async_add_entities(
        [
            LoadIQLoadPowerSensor(coordinator, entry),
            LoadIQAverageRuntimeSensor(coordinator, entry),
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
        }
        segment = data.active_segment
        if segment:
            attrs.update(
                {
                    "active_since": segment.start.isoformat(),
                    "expected_stop": segment.end.isoformat(),
                    "segment_mean_w": segment.mean_power_w,
                }
            )
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
        }
