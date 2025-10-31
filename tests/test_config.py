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
