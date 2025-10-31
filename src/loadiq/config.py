"""
Configuration models for LoadIQ.

The goal is to keep data-access, preprocessing, and detection settings in one
place so we can switch data sources or adjust heuristics without changing code.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, SecretStr


class InfluxConnection(BaseModel):
    """Parameters required to talk to an InfluxDB instance."""

    url: str = Field(..., description="Base URL, e.g. http://localhost:8086")
    token: SecretStr
    org: str
    bucket: str
    verify_ssl: bool = True
    timeout_s: float = Field(default=30.0, ge=0.0)


class EntityRef(BaseModel):
    """Metadata for a Home Assistant measurement stored in InfluxDB."""

    measurement: str = Field(default="W")
    field: str = Field(default="value")
    entity_id: str
    domain: str = Field(default="sensor")
    aggregate_every: str = Field(default="10s", description="Window for aggregateWindow")


class KnownLoadConfig(BaseModel):
    """Configuration for a directly metered load (e.g. EV charger)."""

    name: str
    entity: EntityRef
    subtract_from_house: bool = True


class EntitiesConfig(BaseModel):
    """Collection of entity references used by the pipeline."""

    house_power: EntityRef
    outdoor_temp: Optional[EntityRef] = None
    known_loads: List[KnownLoadConfig] = Field(default_factory=list)


class DetectionConfig(BaseModel):
    """Parameters controlling rule-based detection."""

    min_power_w: float = Field(default=2000.0, ge=0.0)
    max_power_w: float = Field(default=3500.0, ge=0.0)
    min_duration_s: float = Field(default=300.0, ge=0.0)
    max_duration_s: float = Field(default=3600.0, ge=0.0)
    min_off_duration_s: float = Field(default=600.0, ge=0.0)
    smoothing_window: int = Field(default=6, ge=1, description="Number of samples for rolling mean")
    baseline_window: int = Field(default=180, ge=1, description="Samples for rolling median baseline")
    start_delta_w: float = Field(default=400.0, ge=0.0, description="Minimum positive delta to detect ramp-up")
    stop_delta_w: float = Field(default=400.0, ge=0.0, description="Minimum negative delta magnitude to detect shut-down")
    spike_tolerance_ratio: float = Field(default=0.25, ge=0.0, description="Allowed relative deviation from segment baseline before flagging spike")
    spike_tolerance_w: float = Field(default=400.0, ge=0.0, description="Minimum absolute deviation before flagging spike")
    spike_min_duration_s: float = Field(default=30.0, ge=0.0, description="Duration a spike must persist before counting energy")


class LoadIQConfig(BaseModel):
    """Top-level configuration container."""

    influx: InfluxConnection
    entities: EntitiesConfig
    detection: DetectionConfig = Field(default_factory=DetectionConfig)

    @classmethod
    def from_file(cls, path: Path) -> "LoadIQConfig":
        """Load configuration from JSON or YAML."""
        import json
        import yaml

        with path.open("r", encoding="utf8") as fh:
            text = fh.read()

        for loader, suffixes in (
            (yaml.safe_load, (".yaml", ".yml")),
            (json.loads, (".json",)),
        ):
            if path.suffix.lower() in suffixes:
                data = loader(text)
                break
        else:
            data = yaml.safe_load(text)

        return cls.model_validate(data)

    @classmethod
    def from_env(cls) -> "LoadIQConfig":
        """Build configuration from environment variables."""
        import os

        def must_get(key: str) -> str:
            value = os.getenv(key)
            if not value:
                raise ValueError(f"Missing required environment variable: {key}")
            return value

        house_entity = EntityRef(entity_id=must_get("LOADIQ_HOUSE_ENTITY"))

        known_loads: List[KnownLoadConfig] = []
        ev_entity = os.getenv("LOADIQ_EV_ENTITY")
        if ev_entity:
            known_loads.append(
                KnownLoadConfig(
                    name="ev",
                    entity=EntityRef(entity_id=ev_entity),
                    subtract_from_house=True,
                )
            )

        outdoor = os.getenv("LOADIQ_OUTDOOR_ENTITY")
        outdoor_ref = EntityRef(entity_id=outdoor) if outdoor else None

        return cls(
            influx=InfluxConnection(
                url=must_get("LOADIQ_INFLUX_URL"),
                token=SecretStr(must_get("LOADIQ_INFLUX_TOKEN")),
                org=must_get("LOADIQ_INFLUX_ORG"),
                bucket=must_get("LOADIQ_INFLUX_BUCKET"),
                verify_ssl=os.getenv("LOADIQ_INFLUX_VERIFY_SSL", "true").lower() == "true",
            ),
            entities=EntitiesConfig(
                house_power=house_entity,
                outdoor_temp=outdoor_ref,
                known_loads=known_loads,
            ),
        )
