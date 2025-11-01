"""Command-line interface for LoadIQ."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from ..config import LoadIQConfig
from ..data.source import InfluxDBSource, PowerDataSource
from ..detection.segments import (
    DetectedSegment,
    detect_heatpump_segments,
    detect_segments,
)
from ..preprocessing.align import add_derived_columns, assemble_power_frame


def _parse_timestamp(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def _load_config(path: Optional[Path]) -> LoadIQConfig:
    if path:
        return LoadIQConfig.from_file(path)
    return LoadIQConfig.from_env()


def _fetch_all(
    source: PowerDataSource,
    cfg: LoadIQConfig,
    start: pd.Timestamp,
    end: pd.Timestamp,
    aggregate: str,
) -> Dict[str, pd.DataFrame]:
    data: Dict[str, pd.DataFrame] = {}
    data["house"] = source.fetch_series(cfg.entities.house_power, start.to_pydatetime(), end.to_pydatetime(), aggregate)
    known = {}
    for load in cfg.entities.known_loads:
        df = source.fetch_series(load.entity, start.to_pydatetime(), end.to_pydatetime(), aggregate)
        known[load.name] = df
    data["known"] = known
    if cfg.entities.outdoor_temp:
        data["outdoor"] = source.fetch_series(cfg.entities.outdoor_temp, start.to_pydatetime(), end.to_pydatetime(), aggregate)
    else:
        data["outdoor"] = None
    return data


def _segments_to_frame(segments: List[DetectedSegment]) -> pd.DataFrame:
    return pd.DataFrame([seg.to_dict() for seg in segments])


def handle_detect(args: argparse.Namespace) -> None:
    cfg = _load_config(args.config)
    start = _parse_timestamp(args.since)
    end = _parse_timestamp(args.until)

    aggregate = args.freq or cfg.entities.house_power.aggregate_every
    with InfluxDBSource(cfg.influx) as source:
        payload = _fetch_all(source, cfg, start, end, aggregate)

    frame = assemble_power_frame(
        house=payload["house"],
        known_loads=payload["known"],
        temp=payload["outdoor"],
        freq=aggregate,
    )
    det_updates = {}
    if args.min_power is not None:
        det_updates["min_power_w"] = args.min_power
    if args.min_duration is not None:
        det_updates["min_duration_s"] = args.min_duration

    detection_cfg = cfg.detection.model_copy(update=det_updates) if det_updates else cfg.detection

    frame = add_derived_columns(
        frame,
        smoothing_window=detection_cfg.smoothing_window,
        baseline_window=detection_cfg.baseline_window,
    )

    if args.mode == "simple":
        segments = detect_segments(
            frame,
            min_power_w=detection_cfg.min_power_w,
            min_duration_s=detection_cfg.min_duration_s,
        )
    else:
        segments = detect_heatpump_segments(frame, detection_cfg)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _segments_to_frame(segments).to_csv(output_path, index=False)

    print(f"Detected {len(segments)} segments between {start.isoformat()} and {end.isoformat()}")
    if not segments:
        return

    total_energy_raw = sum(seg.energy_kwh for seg in segments)
    total_energy_clamped = sum(seg.clamped_energy_kwh for seg in segments)
    avg_duration = sum(seg.duration.total_seconds() for seg in segments) / len(segments) / 60
    print(f"Total energy (net contribution): {total_energy_raw:.2f} kWh | clamped: {total_energy_clamped:.2f} kWh")
    print(f"Average duration: {avg_duration:.1f} minutes")

    top = sorted(segments, key=lambda seg: seg.mean_power_w, reverse=True)[: min(5, len(segments))]
    print("Top segments by mean power:")
    for seg in top:
        spike_note = ""
        if getattr(seg, "has_spike", False):
            spike_note = f" (spike {seg.spike_energy_kwh:.3f} kWh)"
        clamped_peak = getattr(seg, "clamped_peak_w", seg.peak_power_w)
        print(
            f"  {seg.start.isoformat()} -> {seg.end.isoformat()} | "
            f"mean {seg.mean_power_w:.0f} W, peak {clamped_peak:.0f} W (clamped), "
            f"raw {seg.energy_kwh:.2f} kWh, clamped {seg.clamped_energy_kwh:.2f} kWh{spike_note}"
        )

    if args.json:
        print(json.dumps([seg.to_dict() for seg in segments], indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LoadIQ CLI")
    parser.add_argument("--config", type=Path, help="Path to config file (JSON or YAML)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect", help="Detect target load segments")
    detect.add_argument("--since", required=True, help="Start timestamp (ISO8601)")
    detect.add_argument("--until", required=True, help="End timestamp (ISO8601)")
    detect.add_argument("--freq", default="10s", help="Resampling frequency (default: 10s)")
    detect.add_argument("--min-power", type=float, help="Override minimum power threshold in watts")
    detect.add_argument("--min-duration", type=float, help="Override minimum duration in seconds")
    detect.add_argument(
        "--mode",
        choices=["adaptive", "simple"],
        default="adaptive",
        help="Detection mode (adaptive heat pump heuristics or simple threshold)",
    )
    detect.add_argument("--output", help="Write detected segments to CSV")
    detect.add_argument("--json", action="store_true", help="Print detected segments as JSON")
    detect.set_defaults(func=handle_detect)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
