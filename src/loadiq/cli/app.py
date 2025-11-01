"""User-friendly command line for LoadIQ with tab completion support."""

from __future__ import annotations

import difflib
import json
import re
import os
from pathlib import Path
from typing import Callable, Optional, Sequence
import warnings

import click
import pandas as pd

from ..config import LoadIQConfig
from ..data.source import InfluxDBSource
from ..detection.segments import DetectedSegment, detect_heatpump_segments
from ..preprocessing.align import assemble_power_frame, add_derived_columns

try:
    from influxdb_client.client.warnings import MissingPivotFunction
except Exception:  # pragma: no cover - optional dependency details
    MissingPivotFunction = None


def _suppress_missing_pivot_warnings() -> None:
    if MissingPivotFunction is None:
        return
    warnings.simplefilter("ignore", MissingPivotFunction)
    warnings.filterwarnings("ignore", category=MissingPivotFunction)
    warnings.filterwarnings(
        "ignore",
        message="The query doesn't contains the pivot() function.",
        category=UserWarning,
        module="influxdb_client.client.warnings",
    )


_suppress_missing_pivot_warnings()

DEFAULT_CONFIG = Path("config/local.yaml")
DEFAULT_WINDOW = pd.Timedelta(hours=3)


def _project_root() -> Path:
    try:
        return Path(__file__).resolve().parents[3]
    except IndexError:  # pragma: no cover - fallback for unusual layouts
        return Path.cwd()


def _default_config_candidates() -> list[Path]:
    roots = [Path.cwd()]
    root_from_module = _project_root()
    if root_from_module not in roots:
        roots.append(root_from_module)
    home = Path.home()
    candidates = [
        root / "config" / "local.yaml"
        for root in roots
    ] + [
        root / "config" / "example.yaml"
        for root in roots
    ] + [
        home / ".config" / "loadiq" / "config.yaml",
        home / ".loadiq" / "config.yaml",
    ]
    seen: list[Path] = []
    result: list[Path] = []
    for path in candidates:
        expanded = path.expanduser()
        if expanded not in seen:
            seen.append(expanded)
            result.append(expanded)
    return result


def _discover_config(explicit: Optional[Path]) -> tuple[LoadIQConfig, Optional[Path]]:
    if explicit:
        expanded = explicit.expanduser()
        if not expanded.exists():
            raise click.ClickException(f"Config file '{expanded}' not found.")
        return LoadIQConfig.from_file(expanded), expanded

    env_path = os.getenv("LOADIQ_CONFIG")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.exists():
            return LoadIQConfig.from_file(candidate), candidate

    for candidate in _default_config_candidates():
        if candidate.exists():
            return LoadIQConfig.from_file(candidate), candidate

    try:
        cfg = LoadIQConfig.from_env()
        return cfg, None
    except Exception as exc:
        raise click.ClickException(
            "No LoadIQ config found. Provide --config, set LOADIQ_CONFIG, "
            "or export the required LOADIQ_* environment variables."
        ) from exc

_DURATION_PRESETS: dict[str, pd.Timedelta] = {
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "45m": pd.Timedelta(minutes=45),
    "1h": pd.Timedelta(hours=1),
    "2h": pd.Timedelta(hours=2),
    "3h": pd.Timedelta(hours=3),
    "6h": pd.Timedelta(hours=6),
    "12h": pd.Timedelta(hours=12),
    "24h": pd.Timedelta(hours=24),
    "1d": pd.Timedelta(days=1),
}

_PRESET_WINDOWS: dict[str, Callable[[pd.Timestamp], tuple[pd.Timestamp, pd.Timestamp]]] = {
    "last-15m": lambda now: (now - pd.Timedelta(minutes=15), now),
    "last-30m": lambda now: (now - pd.Timedelta(minutes=30), now),
    "last-1h": lambda now: (now - pd.Timedelta(hours=1), now),
    "last-3h": lambda now: (now - pd.Timedelta(hours=3), now),
    "last-6h": lambda now: (now - pd.Timedelta(hours=6), now),
    "last-12h": lambda now: (now - pd.Timedelta(hours=12), now),
    "last-24h": lambda now: (now - pd.Timedelta(hours=24), now),
    "today": lambda now: (now.normalize(), now),
    "yesterday": lambda now: ((now - pd.Timedelta(days=1)).normalize(), now.normalize()),
}

WINDOW_SYNTAX_HELP = (
    "Use quick expressions like 'last-1h', 'yesterday', '6h', '2024-02-01..2024-02-02', or '-6h..-3h'. "
    "You can also write '2024-02-01 08:00 + 2h' to start at a point and extend by a duration."
)

_PLUS_SET_PATTERN = re.compile(r"^(?P<start>.+?)\s+\+\s*(?P<duration>.+)$")


def _window_suggestion_candidates() -> list[str]:
    baseline = {
        *(_PRESET_WINDOWS.keys()),
        *(_DURATION_PRESETS.keys()),
        "default",
        "auto",
        "now",
        "today",
        "yesterday",
        "6h",
        "-6h..-3h",
        "2024-02-01..2024-02-02",
    }
    return sorted(baseline)


def _suggest_window_expression_text(expression: str) -> str:
    normalized = expression.strip().lower()
    candidates = _window_suggestion_candidates()
    close_matches = difflib.get_close_matches(normalized, candidates, n=3, cutoff=0.3)
    if not close_matches:
        prefix_matches = [opt for opt in candidates if opt.startswith(normalized)][:3]
        close_matches = prefix_matches
    if not close_matches:
        close_matches = ["last-1h", "6h", "yesterday"]
    formatted = ", ".join(close_matches)
    return f"Try one of: {formatted}."


class DurationParamType(click.ParamType):
    name = "duration"

    def convert(self, value: str | pd.Timedelta | None, param, ctx) -> pd.Timedelta | None:
        if value is None or isinstance(value, pd.Timedelta):
            return value
        try:
            delta = _parse_duration(value)
        except ValueError as exc:
            self.fail(str(exc), param, ctx)
        return delta


def _utc_now() -> pd.Timestamp:
    now = pd.Timestamp.utcnow().floor("min")
    return now if now.tzinfo else now.tz_localize("UTC")


def _parse_timestamp(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def _parse_duration(value: str) -> pd.Timedelta:
    key = value.strip().lower()
    if not key:
        raise ValueError("Duration cannot be empty.")
    normalized = key.replace(" ", "")
    if normalized in _DURATION_PRESETS:
        return _DURATION_PRESETS[normalized]
    if key in _DURATION_PRESETS:
        return _DURATION_PRESETS[key]
    try:
        delta = pd.to_timedelta(key)
        if pd.isna(delta) or delta <= pd.Timedelta(0):
            raise ValueError
        return delta
    except Exception:
        pass

    pattern = re.compile(r"(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>w|weeks?|d|days?|h|hours?|hrs?|m|minutes?|mins?|s|seconds?|secs?)")
    total_seconds = 0.0
    matched = False
    for match in pattern.finditer(key):
        matched = True
        amount = float(match.group("value"))
        unit = match.group("unit")
        if unit.startswith("w"):
            total_seconds += amount * 7 * 24 * 3600
        elif unit.startswith("d"):
            total_seconds += amount * 24 * 3600
        elif unit.startswith("h") or unit.startswith("hr"):
            total_seconds += amount * 3600
        elif unit.startswith("m"):
            total_seconds += amount * 60
        else:
            total_seconds += amount
    if matched and total_seconds > 0:
        return pd.Timedelta(seconds=total_seconds)
    raise ValueError(f"Cannot interpret duration '{value}'. Try formats like '90m', '2h30m', or '1 day'.")


def _parse_friendly_timestamp(value: str, reference: pd.Timestamp) -> pd.Timestamp:
    if not value:
        raise click.BadParameter("Timestamp cannot be empty.")
    text = value.strip()
    lowered = text.lower()
    if lowered in {"now", "utc"}:
        return reference
    if lowered == "today":
        return reference.normalize()
    if lowered == "yesterday":
        return (reference - pd.Timedelta(days=1)).normalize()
    if lowered.startswith("-") or lowered.startswith("+"):
        sign = -1 if lowered[0] == "-" else 1
        delta = _parse_duration(lowered[1:])
        ts = reference + (delta if sign > 0 else -delta)
        return ts
    try:
        ts = _parse_timestamp(text)
    except Exception as exc:
        raise click.BadParameter(
            f"Could not parse timestamp '{value}'. Use ISO8601 or relative forms like '-2h' or 'yesterday'."
        ) from exc
    return ts


def _parse_window_expression(expression: str, now: pd.Timestamp, default_window: pd.Timedelta) -> tuple[pd.Timestamp, pd.Timestamp]:
    text = expression.strip()
    if not text:
        return now - default_window, now
    lowered = text.lower()

    if lowered in {"default", "auto"}:
        return now - default_window, now

    if lowered in _PRESET_WINDOWS:
        return _PRESET_WINDOWS[lowered](now)

    if lowered.startswith("last-") and lowered not in _PRESET_WINDOWS:
        delta_text = lowered.removeprefix("last-")
        delta = _parse_duration(delta_text)
        return now - delta, now

    try:
        delta = _parse_duration(lowered)
        return now - delta, now
    except ValueError:
        pass

    if ".." in text:
        left_raw, right_raw = text.split("..", 1)
        right = right_raw.strip()
        end = _parse_friendly_timestamp(right, now) if right else now
        left = left_raw.strip()
        start_reference = end
        start = _parse_friendly_timestamp(left, start_reference) if left else end - default_window
        return start, end

    plus_match = _PLUS_SET_PATTERN.match(text)
    if plus_match:
        start_text = plus_match.group("start").strip()
        duration_text = plus_match.group("duration").strip()
        start = _parse_friendly_timestamp(start_text, now)
        delta = _parse_duration(duration_text)
        end = start + delta
        return start, end

    suggestion = _suggest_window_expression_text(expression)
    raise click.BadParameter(
        f"Could not interpret window '{expression}'. {suggestion} {WINDOW_SYNTAX_HELP}"
    )


def _ensure_time_window(
    window_expr: Optional[str],
    since: Optional[str],
    until: Optional[str],
    hours: Optional[float],
    window: Optional[pd.Timedelta],
    default_window: pd.Timedelta = DEFAULT_WINDOW,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    now = _utc_now()
    end_reference = _parse_friendly_timestamp(until, now) if until else now

    if window_expr:
        if any(opt is not None for opt in (since, until, window, hours)):
            raise click.BadParameter("WINDOW argument cannot be combined with --since/--until/--window/--hours.")
        return _parse_window_expression(window_expr, now, default_window)

    explicit_window = window
    if explicit_window is None and hours:
        if hours <= 0:
            raise click.BadParameter("--hours must be positive.")
        explicit_window = pd.Timedelta(hours=hours)

    if since:
        start = _parse_friendly_timestamp(since, end_reference)
        if explicit_window and not until:
            end = start + explicit_window
        else:
            end = end_reference
    else:
        end = end_reference
        window_to_use = explicit_window or default_window
        start = end - window_to_use

    if start >= end:
        raise click.BadParameter("Start time must be before end time.")
    return start, end


def _load_segments(cfg: LoadIQConfig, start: pd.Timestamp, end: pd.Timestamp) -> list[DetectedSegment]:
    _suppress_missing_pivot_warnings()
    try:
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
    except Exception as exc:  # pragma: no cover - network errors vary
        raise click.ClickException(
            f"Could not reach InfluxDB at {cfg.influx.url}: {exc}"
        ) from exc
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
    run_count = df.shape[0]
    total_duration = float(df["duration_min"].sum())
    total_energy_raw = float(df["energy_kwh_raw"].sum())
    total_energy_clamped = float(df["energy_kwh_clamped"].sum())
    total_spike_energy = float(df["spike_energy_kwh"].sum())
    avg_mean_power = float(df["mean_power_w"].mean()) if not df["mean_power_w"].empty else 0.0
    max_peak_power = float(df["clamped_peak_w"].max()) if not df["clamped_peak_w"].empty else 0.0
    spike_runs = int(df["has_spike"].sum())

    df["start"] = df["start"].dt.tz_convert("Europe/Stockholm").dt.strftime("%Y-%m-%d %H:%M")
    df["end"] = df["end"].dt.tz_convert("Europe/Stockholm").dt.strftime("%Y-%m-%d %H:%M")
    df["duration_min"] = df["duration_min"].round(1)
    df["mean_power_w"] = df["mean_power_w"].round(0)
    df["clamped_peak_w"] = df["clamped_peak_w"].round(0)
    df["energy_kwh_raw"] = df["energy_kwh_raw"].round(2)
    df["energy_kwh_clamped"] = df["energy_kwh_clamped"].round(2)
    df["spike_energy_kwh"] = df["spike_energy_kwh"].round(3)
    total_row = {
        "start": f"Total ({run_count} runs)",
        "end": "",
        "duration_min": round(total_duration, 1),
        "mean_power_w": round(avg_mean_power, 0) if run_count else "",
        "clamped_peak_w": round(max_peak_power, 0) if run_count else "",
        "energy_kwh_raw": round(total_energy_raw, 2),
        "energy_kwh_clamped": round(total_energy_clamped, 2),
        "spike_energy_kwh": round(total_spike_energy, 3),
        "has_spike": spike_runs,
    }
    df = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
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
        cfg, resolved_path = _discover_config(ctx.obj.get("CONFIG_PATH"))
        ctx.obj["CONFIG"] = cfg
        ctx.obj["CONFIG_PATH"] = resolved_path
    return cfg


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    help="Path to LoadIQ configuration file. "
    "Defaults to config/local.yaml, LOADIQ_CONFIG, or LOADIQ_* environment variables.",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[Path]) -> None:
    """LoadIQ control utility."""
    ctx.ensure_object(dict)
    ctx.obj["CONFIG_PATH"] = config_path
    ctx.obj["CONFIG"] = None


@cli.command("runs")
@click.argument("window_expr", required=False, metavar="[WINDOW]")
@click.option(
    "--since",
    help="Start timestamp (ISO8601) or relative form like '-6h' or 'yesterday'.",
    hidden=True,
)
@click.option(
    "--until",
    help="End timestamp (ISO8601) or relative form like 'now', '-15m', or 'today'.",
    hidden=True,
)
@click.option(
    "--window",
    "-w",
    "window",
    type=DurationParamType(),
    help="Lookback window (e.g. '45m', '2h30m', '1 day').",
    hidden=True,
)
@click.option(
    "--hours",
    type=float,
    help="Window length in hours (legacy shortcut; defaults to 3h if nothing else is provided).",
    hidden=True,
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of table")
@click.pass_context
def runs_cmd(
    ctx: click.Context,
    window_expr: Optional[str],
    since: Optional[str],
    until: Optional[str],
    window: Optional[pd.Timedelta],
    hours: Optional[float],
    json_output: bool,
) -> None:
    """Show detected runs for a time window (default: last 3 hours).

    WINDOW accepts quick expressions such as 'last-1h', 'yesterday', '6h', '-6h..-3h', or '2024-02-01..2024-02-02'.
    """
    start, end = _ensure_time_window(window_expr, since, until, hours, window)
    cfg = _get_config(ctx)
    segments = _load_segments(cfg, start, end)
    df = _segments_to_frame(segments)
    if json_output:
        click.echo(json.dumps(df.to_dict(orient="records"), indent=2, default=str))
    else:
        click.echo(_format_table(df))


@cli.command("detect")
@click.argument("window_expr", required=False, metavar="[WINDOW]")
@click.option(
    "--since",
    help="Start timestamp (ISO8601) or relative form like '-12h' or 'yesterday'.",
    hidden=True,
)
@click.option(
    "--until",
    help="End timestamp (ISO8601) or relative form like 'now', '-30m', or 'today'.",
    hidden=True,
)
@click.option(
    "--window",
    "-w",
    "window",
    type=DurationParamType(),
    help="Lookback window (e.g. '6h', '1d', '2h30m').",
    hidden=True,
)
@click.option(
    "--hours",
    type=float,
    help="Window length in hours (legacy shortcut; defaults to 24h if nothing else is provided).",
    hidden=True,
)
@click.option("--output", type=click.Path(path_type=Path), help="Write results to CSV/JSON depending on extension")
@click.option("--mode", type=click.Choice(["table", "json"]), default="table", show_default=True)
@click.pass_context
def detect_cmd(
    ctx: click.Context,
    window_expr: Optional[str],
    since: Optional[str],
    until: Optional[str],
    window: Optional[pd.Timedelta],
    hours: Optional[float],
    output: Optional[Path],
    mode: str,
) -> None:
    """Run detection for a window and optionally export results.

    WINDOW shares the same syntax as the runs command (e.g. 'last-24h', 'yesterday', '2024-02-01..2024-02-02').
    """
    start, end = _ensure_time_window(
        window_expr,
        since,
        until,
        hours,
        window,
        default_window=pd.Timedelta(hours=24),
    )
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

    daily_total_row = pd.Series(
        {
            "runs": overall["total_runs"],
            "runtime_min": overall["total_runtime_hours"] * 60,
            "avg_duration_min": overall["avg_duration_min"],
            "mean_temp_c": float(daily["mean_temp_c"].mean()) if "mean_temp_c" in daily.columns else pd.NA,
            "energy_kwh_raw": overall["total_energy_raw_kwh"],
            "energy_kwh_clamped": overall["total_energy_clamped_kwh"],
        },
        name="Total",
    )
    daily_with_total = pd.concat([daily, daily_total_row.to_frame().T])

    if json_output:
        payload = {
            "overall": overall,
            "daily": daily_with_total.reset_index().to_dict(orient="records"),
        }
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo("Overall stats")
        for key, value in overall.items():
            click.echo(f"  {key.replace('_', ' ')}: {value}")
        click.echo("\nDaily summary:")
        click.echo(daily_with_total.to_string())



def main() -> None:
    cli()


if __name__ == "__main__":
    main()
