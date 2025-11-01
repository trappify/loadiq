"""Factories for instantiating power data sources."""

from __future__ import annotations

from typing import Any

from .source import InfluxDBSource, PowerDataSource
from ..config import DataBackend, LoadIQConfig


def create_power_data_source(cfg: LoadIQConfig, hass: Any | None = None) -> PowerDataSource:
    """Instantiate the appropriate data source for the given configuration."""
    if cfg.backend == DataBackend.INFLUXDB:
        if cfg.influx is None:
            raise ValueError("InfluxDB configuration missing for backend 'influxdb'.")
        return InfluxDBSource(cfg.influx)

    if cfg.backend == DataBackend.HOME_ASSISTANT:
        if hass is None:
            raise ValueError("Home Assistant backend requires a Home Assistant instance.")
        from .homeassistant import HomeAssistantHistorySource

        return HomeAssistantHistorySource(hass)

    raise NotImplementedError(
        f"Backend '{cfg.backend.value}' is not supported in the standalone CLI. "
        "Use the Home Assistant integration to consume sensors directly."
    )
