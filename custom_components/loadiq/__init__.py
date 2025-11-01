"""LoadIQ Home Assistant integration."""

from __future__ import annotations

import sys
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

vendor_path = Path(__file__).parent / "vendor"
if str(vendor_path) not in sys.path:
    sys.path.insert(0, str(vendor_path))

from .const import DATA_CONFIG, DATA_COORDINATOR, DOMAIN, PLATFORMS
from .coordinator import LoadIQDataCoordinator


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up LoadIQ from YAML (not supported)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LoadIQ from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    coordinator = LoadIQDataCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    if not entry.options:
        hass.config_entries.async_update_entry(entry, options=dict(entry.data))

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
        DATA_CONFIG: entry.options or entry.data,
    }

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a LoadIQ config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle reloading a config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
