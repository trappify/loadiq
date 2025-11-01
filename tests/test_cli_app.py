import warnings

import pandas as pd
import pytest

from click.testing import CliRunner

from loadiq.cli.app import cli
from loadiq.cli import app as cli_module
from loadiq.preprocessing.align import assemble_power_frame


def _sample_segments_df() -> pd.DataFrame:
    start1 = pd.Timestamp("2025-01-01T12:00:00Z")
    end1 = start1 + pd.Timedelta(minutes=10)
    start2 = pd.Timestamp("2025-01-01T20:00:00Z")
    end2 = start2 + pd.Timedelta(minutes=15)
    return pd.DataFrame(
        [
            {
                "start": start1,
                "end": end1,
                "duration_min": 10.0,
                "mean_power_w": 3200.0,
                "clamped_peak_w": 3600.0,
                "energy_kwh_raw": 0.6,
                "energy_kwh_clamped": 0.58,
                "spike_energy_kwh": 0.0,
                "has_spike": False,
                "temperature_c": 2.0,
            },
            {
                "start": start2,
                "end": end2,
                "duration_min": 15.0,
                "mean_power_w": 3000.0,
                "clamped_peak_w": 3400.0,
                "energy_kwh_raw": 0.8,
                "energy_kwh_clamped": 0.78,
                "spike_energy_kwh": 0.02,
                "has_spike": True,
                "temperature_c": 1.5,
            },
        ]
    )


def test_cli_help_works(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["--config", "config/example.yaml", "--help"])
    assert result.exit_code == 0
    assert "LoadIQ control utility" in result.output


def test_default_window_is_three_hours(monkeypatch):
    fake_now = pd.Timestamp("2024-01-01T12:00:00Z")
    monkeypatch.setattr(cli_module, "_utc_now", lambda: fake_now)
    start, end = cli_module._ensure_time_window(
        window_expr=None,
        since=None,
        until=None,
        hours=None,
        window=None,
    )
    assert start == fake_now - pd.Timedelta(hours=3)
    assert end == fake_now


def test_relative_since_and_window_expand(monkeypatch):
    fake_now = pd.Timestamp("2024-01-01T12:00:00Z")
    monkeypatch.setattr(cli_module, "_utc_now", lambda: fake_now)
    start, end = cli_module._ensure_time_window(
        window_expr=None,
        since="-6h",
        until=None,
        hours=None,
        window=pd.Timedelta(hours=2),
    )
    assert start == fake_now - pd.Timedelta(hours=6)
    assert end == fake_now - pd.Timedelta(hours=4)


def test_custom_default_window(monkeypatch):
    fake_now = pd.Timestamp("2024-01-01T12:00:00Z")
    monkeypatch.setattr(cli_module, "_utc_now", lambda: fake_now)
    start, end = cli_module._ensure_time_window(
        window_expr=None,
        since=None,
        until=None,
        hours=None,
        window=None,
        default_window=pd.Timedelta(hours=24),
    )
    assert start == fake_now - pd.Timedelta(hours=24)
    assert end == fake_now


def test_window_argument_shortcut(monkeypatch):
    fake_now = pd.Timestamp("2024-01-01T12:00:00Z")
    monkeypatch.setattr(cli_module, "_utc_now", lambda: fake_now)
    start, end = cli_module._ensure_time_window(
        window_expr="last-1h",
        since=None,
        until=None,
        hours=None,
        window=None,
    )
    assert start == fake_now - pd.Timedelta(hours=1)
    assert end == fake_now


def test_window_range_expression(monkeypatch):
    fake_now = pd.Timestamp("2024-01-02T12:00:00Z")
    monkeypatch.setattr(cli_module, "_utc_now", lambda: fake_now)
    start, end = cli_module._ensure_time_window(
        window_expr="2024-01-01..2024-01-02",
        since=None,
        until=None,
        hours=None,
        window=None,
    )
    assert start == pd.Timestamp("2024-01-01T00:00:00Z")
    assert end == pd.Timestamp("2024-01-02T00:00:00Z")


def test_window_argument_conflicts_raise(monkeypatch):
    fake_now = pd.Timestamp("2024-01-01T12:00:00Z")
    monkeypatch.setattr(cli_module, "_utc_now", lambda: fake_now)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--config",
            "config/local.yaml",
            "runs",
            "last-1h",
            "--since",
            "-2h",
        ],
    )
    assert result.exit_code != 0
    assert "WINDOW argument cannot be combined" in result.output


def test_runs_surfaces_connection_errors(monkeypatch):
    class BoomSource:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("boom")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(cli_module, "InfluxDBSource", BoomSource)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--config",
            "config/local.yaml",
            "runs",
        ],
    )
    assert result.exit_code != 0
    assert "Could not reach InfluxDB" in result.output


def test_invalid_window_suggests_options(monkeypatch):
    class DummySource:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def fetch_series(self, *args, **kwargs):
            raise AssertionError("fetch_series should not be called for invalid window")

    monkeypatch.setattr(cli_module, "InfluxDBSource", DummySource)
    runner = CliRunner()
    result = runner.invoke(cli, ["runs", "th"])
    assert result.exit_code != 0
    assert "Try one of:" in result.output
    assert "last-1h" in result.output


def test_runs_table_includes_totals(monkeypatch):
    sample_df = _sample_segments_df()

    monkeypatch.setattr(cli_module, "_get_config", lambda ctx: object())

    def fake_load_segments(cfg, start, end):
        return None

    monkeypatch.setattr(cli_module, "_load_segments", fake_load_segments)
    monkeypatch.setattr(cli_module, "_segments_to_frame", lambda segments: sample_df.copy())

    runner = CliRunner()
    result = runner.invoke(cli, ["runs"])
    assert result.exit_code == 0
    assert "Total (2 runs)" in result.output
    assert "1.4" in result.output  # total duration


def test_missing_pivot_warning_suppressed():
    try:
        from influxdb_client.client.warnings import MissingPivotFunction
    except Exception:  # pragma: no cover - dependency optional
        pytest.skip("MissingPivotFunction warning class unavailable")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        cli_module._suppress_missing_pivot_warnings()
        MissingPivotFunction.print_warning('from(bucket: "foo")')
    assert caught == []


def test_assemble_power_frame_no_future_warnings():
    index = pd.date_range("2025-01-01", periods=12, freq="10s", tz="UTC")
    house = pd.DataFrame({"value": [float(i) for i in range(12)]}, index=index)
    known = {
        "ev": pd.DataFrame({"value": [0.0 for _ in range(12)]}, index=index),
    }
    temp = pd.DataFrame({"value": [2.0 for _ in range(12)]}, index=index)
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        assemble_power_frame(house, known, temp, freq="10s")


def test_discover_config_prefers_env_file(tmp_path, monkeypatch):
    cfg_path = tmp_path / "custom.yaml"
    cfg_path.write_text(
        """
influx:
  url: http://localhost:8086
  token: fake-token
  org: org
  bucket: bucket
entities:
  house_power:
    entity_id: sensor.house
""",
        encoding="utf8",
    )
    monkeypatch.setenv("LOADIQ_CONFIG", str(cfg_path))
    cfg, resolved = cli_module._discover_config(None)
    assert resolved == cfg_path
    assert cfg.influx.url == "http://localhost:8086"


def test_discover_config_uses_env_variables(monkeypatch):
    monkeypatch.delenv("LOADIQ_CONFIG", raising=False)
    monkeypatch.setattr(cli_module, "_default_config_candidates", lambda: [])
    monkeypatch.setenv("LOADIQ_INFLUX_URL", "http://localhost:8086")
    monkeypatch.setenv("LOADIQ_INFLUX_TOKEN", "tok")
    monkeypatch.setenv("LOADIQ_INFLUX_ORG", "org")
    monkeypatch.setenv("LOADIQ_INFLUX_BUCKET", "bucket")
    monkeypatch.setenv("LOADIQ_HOUSE_ENTITY", "sensor.house")
    cfg, resolved = cli_module._discover_config(None)
    assert resolved is None
    assert cfg.influx.url == "http://localhost:8086"
    assert cfg.influx.token.get_secret_value() == "tok"


def test_stats_table_includes_totals(monkeypatch):
    sample_df = _sample_segments_df()

    monkeypatch.setattr(cli_module, "_get_config", lambda ctx: object())
    monkeypatch.setattr(cli_module, "_load_segments", lambda cfg, start, end: None)
    monkeypatch.setattr(cli_module, "_segments_to_frame", lambda segments: sample_df.copy())

    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--days", "1"])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert any(line.startswith("Total") for line in lines)
