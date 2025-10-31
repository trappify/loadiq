"""User-friendly command line for LoadIQ with tab completion support."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Iterable, Optional, Sequence

import click
import pandas as pd

from ..config import LoadIQConfig
from ..data.source import InfluxDBSource
from ..detection.segments import DetectedSegment, detect_heatpump_segments
from ..preprocessing.align import assemble_power_frame, add_derived_columns

DEFAULT_CONFIG = Path("config/local.yaml")


def _parse_timestamp(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def _ensure_time_window(
    since: Optional[str],
    until: Optional[str],
    hours: Optional[float],
) -> tuple[pd.Timestamp, pd.Timestamp]:
    if since and hours:
        raise click.BadParameter("Use either --since/--until or --hours, not both.")
    now = pd.Timestamp.utcnow().floor("min")
    if since:
        start = _parse_timestamp(since)
        end = _parse_timestamp(until) if until else now
    else:
        duration = timedelta(hours=hours or 24.0)
        end = _parse_timestamp(until) if until else now
        start = end - pd.Timedelta(duration)
    if start >= end:
        raise click.BadParameter("Start time must be before end time.")
    return start, end


def _load_segments(cfg: LoadIQConfig, start: pd.Timestamp, end: pd.Timestamp) -> list[DetectedSegment]:
    with InfluxDBSource(cfg.influx) as source:
        house = source.fetch_series(
            cfg.entities.house_power,
            start.to_pydatetime(),
            end.to_pydatetime(),
            cfg.entities.house_power.aggregate_every,
        )
        known = {
            load.name: source.fetch_series(
                load.entity,
                start.to_pydatetime(),
                end.to_pydatetime(),
                load.entity.aggregate_every,
            )
            for load in cfg.entities.known_loads
        }
        temp = (
            source.fetch_series(
                cfg.entities.outdoor_temp,
                start.to_pydatetime(),
                end.to_pydatetime(),
                cfg.entities.outdoor_temp.aggregate_every,
            )
            if cfg.entities.outdoor_temp
            else None
        )
    frame = assemble_power_frame(
        house=house,
        known_loads=known,
        temp=temp,
        freq=cfg.entities.house_power.aggregate_every,
    )
    frame = add_derived_columns(frame, cfg.detection.smoothing_window, cfg.detection.baseline_window)
    return detect_heatpump_segments(frame, cfg.detection)


def _segments_to_frame(segments: Sequence[DetectedSegment]) -> pd.DataFrame:
    if not segments:
        return pd.DataFrame(
            columns=[
                "start",
                "end",
                "duration_min",
                "mean_power_w",
                "clamped_peak_w",
                "energy_kwh_raw",
                "energy_kwh_clamped",
                "spike_energy_kwh",
                "has_spike",
            ]
        )
    records = []
    for seg in segments:
        records.append(
            {
                "start": seg.start,
                "end": seg.end,
                "duration_min": seg.duration.total_seconds() / 60,
                "mean_power_w": seg.mean_power_w,
                "clamped_peak_w": getattr(seg, "clamped_peak_w", seg.peak_power_w),
                "energy_kwh_raw": seg.energy_kwh,
                "energy_kwh_clamped": getattr(seg, "clamped_energy_kwh", seg.energy_kwh),
                "spike_energy_kwh": getattr(seg, "spike_energy_kwh", 0.0),
                "has_spike": getattr(seg, "has_spike", False),
                "temperature_c": seg.temperature_c,
            }
        )
    return pd.DataFrame.from_records(records)


def _format_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "No runs detected for the requested window."
    df = df.copy()
    df["start"] = df["start"].dt.tz_convert("Europe/Stockholm").dt.strftime("%Y-%m-%d %H:%M")
    df["end"] = df["end"].dt.tz_convert("Europe/Stockholm").dt.strftime("%Y-%m-%d %H:%M")
    df["duration_min"] = df["duration_min"].round(1)
    df["mean_power_w"] = df["mean_power_w"].round(0)
    df["clamped_peak_w"] = df["clamped_peak_w"].round(0)
    df["energy_kwh_raw"] = df["energy_kwh_raw"].round(2)
    df["energy_kwh_clamped"] = df["energy_kwh_clamped"].round(2)
    df["spike_energy_kwh"] = df["spike_energy_kwh"].round(3)
    columns = [
        "start",
        "end",
        "duration_min",
        "mean_power_w",
        "clamped_peak_w",
        "energy_kwh_raw",
        "energy_kwh_clamped",
        "spike_energy_kwh",
        "has_spike",
    ]
    widths = {col: max(len(col), df[col].astype(str).map(len).max()) for col in columns}
    lines = []
    header = " ".join(f"{col:<{widths[col]}}" for col in columns)
    lines.append(header)
    lines.append(" ".join("-" * widths[col] for col in columns))
    for _, row in df[columns].iterrows():
        lines.append(
            " ".join(
                f"{str(row[col]):<{widths[col]}}"
                for col in columns
            )
        )
    return "\n".join(lines)


def _get_config(ctx: click.Context) -> LoadIQConfig:
    cfg = ctx.obj.get("CONFIG")
    if cfg is None:
        cfg = LoadIQConfig.from_file(ctx.obj["CONFIG_PATH"])
        ctx.obj["CONFIG"] = cfg
    return cfg


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=DEFAULT_CONFIG,
    show_default=True,
    help="Path to LoadIQ configuration file (YAML/JSON).",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Path) -> None:
    """LoadIQ control utility."""
    ctx.ensure_object(dict)
    ctx.obj["CONFIG_PATH"] = config_path
    ctx.obj["CONFIG"] = None


@cli.command("runs")
@click.option("--since", help="ISO8601 start timestamp (default: now - hours)")
@click.option("--until", help="ISO8601 end timestamp (default: now)")
@click.option("--hours", type=float, default=3.0, show_default=True, help="Window length in hours")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of table")
@click.pass_context
def runs_cmd(ctx: click.Context, since: Optional[str], until: Optional[str], hours: Optional[float], json_output: bool) -> None:
    """Show detected runs for a time window (default 3 hours)."""
    start, end = _ensure_time_window(since, until, hours)
    cfg = _get_config(ctx)
    segments = _load_segments(cfg, start, end)
    df = _segments_to_frame(segments)
    if json_output:
        click.echo(json.dumps(df.to_dict(orient="records"), indent=2, default=str))
    else:
        click.echo(_format_table(df))


@cli.command("detect")
@click.option("--since", help="ISO8601 start timestamp (default: now - hours)")
@click.option("--until", help="ISO8601 end timestamp (default: now)")
@click.option("--hours", type=float, help="Window length in hours (default: 24)")
@click.option("--output", type=click.Path(path_type=Path), help="Write results to CSV/JSON depending on extension")
@click.option("--mode", type=click.Choice(["table", "json"]), default="table", show_default=True)
@click.pass_context
def detect_cmd(ctx: click.Context, since: Optional[str], until: Optional[str], hours: Optional[float], output: Optional[Path], mode: str) -> None:
    """Run detection for a window and optionally export results."""
    start, end = _ensure_time_window(since, until, hours or 24.0)
    cfg = _get_config(ctx)
    segments = _load_segments(cfg, start, end)
    df = _segments_to_frame(segments)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.suffix.lower() == ".json":
            output.write_text(df.to_json(orient="records", date_format="iso"))
        else:
            df.to_csv(output, index=False)
        click.echo(f"Wrote {len(df)} rows to {output}")

    if mode == "json":
        click.echo(json.dumps(df.to_dict(orient="records"), indent=2, default=str))
    else:
        click.echo(_format_table(df))


@cli.command("stats")
@click.option("--days", type=int, default=30, show_default=True, help="Number of days back to analyse")
@click.option("--json", "json_output", is_flag=True, help="Emit detailed JSON summary")
@click.pass_context
def stats_cmd(ctx: click.Context, days: int, json_output: bool) -> None:
    """Compute daily run statistics for the past N days."""
    cfg = _get_config(ctx)
    end = pd.Timestamp.utcnow().floor("min")
    start = end - pd.Timedelta(days=days)
    segments = _load_segments(cfg, start, end)
    df = _segments_to_frame(segments)
    if df.empty:
        click.echo("No data found in the requested range.")
        return

    df["date"] = df["start"].dt.tz_convert("Europe/Stockholm").dt.date
    daily = df.groupby("date").agg(
        runs=("duration_min", "count"),
        runtime_min=("duration_min", "sum"),
        avg_duration_min=("duration_min", "mean"),
        mean_temp_c=("temperature_c", "mean"),
        energy_kwh_raw=("energy_kwh_raw", "sum"),
        energy_kwh_clamped=("energy_kwh_clamped", "sum"),
    )
    overall = {
        "total_runs": int(df.shape[0]),
        "total_runtime_hours": float(df["duration_min"].sum() / 60),
        "total_energy_raw_kwh": float(df["energy_kwh_raw"].sum()),
        "total_energy_clamped_kwh": float(df["energy_kwh_clamped"].sum()),
        "avg_runs_per_day": float(daily["runs"].mean()),
        "avg_duration_min": float(df["duration_min"].mean()),
        "median_duration_min": float(df["duration_min"].median()),
        "p95_duration_min": float(df["duration_min"].quantile(0.95)),
        "spike_runs": int(df["has_spike"].sum()),
        "spike_energy_kwh": float(df["spike_energy_kwh"].sum()),
    }

    if json_output:
        payload = {
            "overall": overall,
            "daily": daily.reset_index().to_dict(orient="records"),
        }
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo("Overall stats")
        for key, value in overall.items():
            click.echo(f"  {key.replace('_', ' ')}: {value}")
        click.echo("\nDaily summary:")
        click.echo(daily.to_string())



def main() -> None:
    cli()


if __name__ == "__main__":
    main()
