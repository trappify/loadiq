import os
from pathlib import Path

import pytest

from loadiq.config import (
    DetectionConfig,
    LoadIQConfig,
)


def test_config_from_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
backend: influxdb
influx:
  url: "http://localhost:8086"
  token: "token"
  org: "org"
  bucket: "bucket"
entities:
  house_power:
    entity_id: "sensor.house"
  known_loads: []
""",
        encoding="utf8",
    )

    cfg = LoadIQConfig.from_file(config_path)
    assert cfg.influx.url == "http://localhost:8086"
    assert cfg.entities.house_power.entity_id == "sensor.house"
    assert isinstance(cfg.detection, DetectionConfig)


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOADIQ_INFLUX_URL", "http://example")
    monkeypatch.setenv("LOADIQ_INFLUX_TOKEN", "secret")
    monkeypatch.setenv("LOADIQ_INFLUX_ORG", "org")
    monkeypatch.setenv("LOADIQ_INFLUX_BUCKET", "bucket")
    monkeypatch.delenv("LOADIQ_BACKEND", raising=False)
    monkeypatch.setenv("LOADIQ_HOUSE_ENTITY", "sensor.house")
    monkeypatch.setenv("LOADIQ_EV_ENTITY", "sensor.ev")

    cfg = LoadIQConfig.from_env()
    assert cfg.entities.house_power.entity_id == "sensor.house"
    assert cfg.entities.known_loads[0].entity.entity_id == "sensor.ev"


def test_config_from_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOADIQ_INFLUX_URL", raising=False)
    monkeypatch.setenv("LOADIQ_INFLUX_TOKEN", "secret")
    monkeypatch.setenv("LOADIQ_INFLUX_ORG", "org")
    monkeypatch.setenv("LOADIQ_INFLUX_BUCKET", "bucket")
    monkeypatch.setenv("LOADIQ_HOUSE_ENTITY", "sensor.house")

    with pytest.raises(ValueError):
        LoadIQConfig.from_env()


def test_config_homeassistant_backend_allows_missing_influx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOADIQ_BACKEND", "homeassistant")
    monkeypatch.setenv("LOADIQ_HOUSE_ENTITY", "sensor.house")
    monkeypatch.delenv("LOADIQ_INFLUX_URL", raising=False)
    monkeypatch.delenv("LOADIQ_INFLUX_TOKEN", raising=False)
    monkeypatch.delenv("LOADIQ_INFLUX_ORG", raising=False)
    monkeypatch.delenv("LOADIQ_INFLUX_BUCKET", raising=False)

    cfg = LoadIQConfig.from_env()
    assert cfg.backend.value == "homeassistant"
    assert cfg.influx is None
    assert cfg.entities.house_power.entity_id == "sensor.house"


def test_config_requires_influx_for_influx_backend(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
backend: influxdb
entities:
  house_power:
    entity_id: "sensor.house"
""",
        encoding="utf8",
    )
    with pytest.raises(ValueError):
        LoadIQConfig.from_file(config_path)
