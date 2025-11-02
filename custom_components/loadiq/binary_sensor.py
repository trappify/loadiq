"""Binary sensor platform for LoadIQ."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
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
    data = hass.data[DOMAIN]["entries"][entry.entry_id]
    coordinator: LoadIQDataCoordinator = data[DATA_COORDINATOR]
    async_add_entities([LoadIQActiveBinarySensor(coordinator, entry)])


class LoadIQActiveBinarySensor(LoadIQEntity, BinarySensorEntity):
    """Represents whether the target load is currently running."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: LoadIQDataCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "load_active", "Load Active")

    @property
    def is_on(self) -> bool:
        data: CoordinatorData | None = self.coordinator.data
        return bool(data and data.is_active)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        data: CoordinatorData | None = self.coordinator.data
        if not data:
            return None
        attrs: dict[str, object] = {
            "window_start": data.window_start.isoformat(),
            "window_end": data.window_end.isoformat(),
            "confidence": data.active_confidence,
            "classification": "inactive",
        }
        segment = data.active_segment
        if segment:
            attrs.update(
                {
                    "active_since": segment.start.isoformat(),
                    "expected_stop": segment.end.isoformat(),
                    "classification": segment.classification,
                    "confidence_score": segment.confidence,
                }
            )
        else:
            attrs["confidence_score"] = 0.0
        return attrs
