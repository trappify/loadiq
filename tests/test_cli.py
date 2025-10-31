import json
from pathlib import Path

import pandas as pd

from loadiq.cli.main import main as cli_main


class StubSource:
    def __init__(self, _cfg):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetch_series(self, entity, start, end, aggregate):
        index = pd.date_range(start="2025-01-01T00:00:00Z", periods=6, freq="10s", tz="UTC")
        if entity.entity_id == "sensor.house":
            values = [1000, 1200, 3200, 3100, 1100, 900]
        elif entity.entity_id == "sensor.ev":
            values = [0, 0, 1000, 1000, 0, 0]
        else:  # outdoor temp
            values = [5, 5, 5, 5, 5, 5]
        return pd.DataFrame({"value": values}, index=index)


def test_cli_detect(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
influx:
  url: "http://localhost"
  token: "token"
  org: "org"
  bucket: "bucket"
  verify_ssl: false
entities:
  house_power:
    entity_id: "sensor.house"
  outdoor_temp:
    entity_id: "sensor.temp"
  known_loads:
    - name: "ev"
      entity:
        entity_id: "sensor.ev"
detection:
  min_power_w: 1500
  min_duration_s: 20
  smoothing_window: 2
""",
        encoding="utf8",
    )

    monkeypatch.setattr("loadiq.cli.main.InfluxDBSource", lambda cfg: StubSource(cfg))

    output_path = tmp_path / "segments.csv"
    cli_main(
        [
            "--config",
            str(config_path),
            "detect",
            "--since",
            "2025-01-01T00:00:00Z",
            "--until",
            "2025-01-01T00:01:00Z",
            "--output",
            str(output_path),
            "--json",
        ]
    )

    assert output_path.exists()
    data = output_path.read_text(encoding="utf8")
    assert "mean_power_w" in data
