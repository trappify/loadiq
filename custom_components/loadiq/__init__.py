"""LoadIQ Home Assistant integration."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

vendor_path = Path(__file__).parent / "vendor"
if str(vendor_path) not in sys.path:
    sys.path.insert(0, str(vendor_path))

from .const import (
    DATA_CONFIG,
    DATA_COORDINATOR,
    DATA_STORAGE,
    DOMAIN,
    PLATFORMS,
    SERVICE_MARK_SEGMENT,
    SERVICE_MARK_CURRENT_ACTIVE,
    SERVICE_MARK_CURRENT_INACTIVE,
)
from .coordinator import LoadIQDataCoordinator
from .storage import LoadIQStorage


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up LoadIQ from YAML (not supported)."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {
            "entries": {},
            "service_registered": False,
        }
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LoadIQ from a config entry."""
    domain_data = hass.data.setdefault(
        DOMAIN,
        {
            "entries": {},
            "service_registered": False,
        },
    )
    if "entries" not in domain_data:
        domain_data["entries"] = {}
    if "service_registered" not in domain_data:
        domain_data["service_registered"] = False
    entries = domain_data["entries"]
    storage = LoadIQStorage(hass, entry.entry_id)
    await storage.async_load()
    coordinator = LoadIQDataCoordinator(hass, entry, storage)
    await coordinator.async_config_entry_first_refresh()

    if not entry.options:
        hass.config_entries.async_update_entry(entry, options=dict(entry.data))

    entries[entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
        DATA_CONFIG: entry.options or entry.data,
        DATA_STORAGE: storage,
    }

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    if not domain_data.get("service_registered"):

        async def _async_resolve_entry(call: ServiceCall) -> tuple[str, dict[str, object]]:
            target_entries = hass.data.get(DOMAIN, {}).get("entries", {})
            entry_id = call.data.get("entry_id")
            if entry_id:
                target = target_entries.get(entry_id)
                if target is None:
                    raise HomeAssistantError("Unknown LoadIQ entry_id")
                return entry_id, target
            if len(target_entries) != 1:
                raise HomeAssistantError("Multiple LoadIQ entries configured; specify entry_id.")
            entry_id, target = next(iter(target_entries.items()))
            return entry_id, target

        async def _async_label_segment(target: dict[str, object], label: str, start: pd.Timestamp | None) -> None:
            coordinator: LoadIQDataCoordinator = target[DATA_COORDINATOR]
            data = coordinator.data
            segment = None
            if start is not None:
                segment = coordinator.find_segment_by_start(start)
                if segment is None:
                    raise HomeAssistantError("Segment not found near the provided start time")
            else:
                if data and data.active_segment:
                    segment = data.active_segment
                elif data:
                    segments = list(data.segments)
                    if segments:
                        segment = segments[-1]
                if segment is None:
                    raise HomeAssistantError("No active or recent segment available to mark")

            storage: LoadIQStorage = target[DATA_STORAGE]
            await storage.async_add_label(segment, label)
            await coordinator.async_request_refresh()

        async def _async_handle_mark_segment(call: ServiceCall) -> None:
            entry_id, target = await _async_resolve_entry(call)
            label = call.data.get("label")
            if not label:
                raise HomeAssistantError("Label is required")
            label = str(label).lower()
            if label not in {"heatpump", "other"}:
                raise HomeAssistantError("Label must be 'heatpump' or 'other'")

            start_str = call.data.get("start")
            start_ts: pd.Timestamp | None = None
            if start_str:
                parsed = dt_util.parse_datetime(start_str)
                if parsed is None:
                    raise HomeAssistantError("Invalid start timestamp")
                start_ts = pd.Timestamp(dt_util.as_utc(parsed))

            await _async_label_segment(target, label, start_ts)

        async def _async_handle_mark_active(call: ServiceCall) -> None:
            _, target = await _async_resolve_entry(call)
            await _async_label_segment(target, "heatpump", None)

        async def _async_handle_mark_inactive(call: ServiceCall) -> None:
            _, target = await _async_resolve_entry(call)
            await _async_label_segment(target, "other", None)

        hass.services.async_register(DOMAIN, SERVICE_MARK_SEGMENT, _async_handle_mark_segment)
        hass.services.async_register(DOMAIN, SERVICE_MARK_CURRENT_ACTIVE, _async_handle_mark_active)
        hass.services.async_register(DOMAIN, SERVICE_MARK_CURRENT_INACTIVE, _async_handle_mark_inactive)
        domain_data["service_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a LoadIQ config entry."""
    domain_data = hass.data.get(DOMAIN)
    entries = domain_data.get("entries", {}) if domain_data else {}
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and domain_data:
        entries.pop(entry.entry_id, None)
        if not entries and domain_data.get("service_registered"):
            hass.services.async_remove(DOMAIN, SERVICE_MARK_SEGMENT)
            hass.services.async_remove(DOMAIN, SERVICE_MARK_CURRENT_ACTIVE)
            hass.services.async_remove(DOMAIN, SERVICE_MARK_CURRENT_INACTIVE)
            domain_data["service_registered"] = False
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle reloading a config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
