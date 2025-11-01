"""Base entity definitions for LoadIQ."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, DOMAIN_TITLE
from .coordinator import LoadIQDataCoordinator


class LoadIQEntity(CoordinatorEntity[LoadIQDataCoordinator]):
    """Common attributes shared across LoadIQ entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LoadIQDataCoordinator, entry: ConfigEntry, unique_suffix: str, name_suffix: str) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "manufacturer": DOMAIN_TITLE,
            "name": DOMAIN_TITLE,
        }
        self._attr_name = f"{DOMAIN_TITLE} {name_suffix}"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
