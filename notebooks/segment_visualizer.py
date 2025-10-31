"""Quick plotting helper for LoadIQ segments."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import pandas as pd

from loadiq.config import LoadIQConfig
from loadiq.data.source import InfluxDBSource
from loadiq.preprocessing.align import assemble_power_frame, add_derived_columns


def load_frame(cfg: LoadIQConfig, start: str, end: str, freq: str = "10s") -> pd.DataFrame:
    start_ts = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
    with InfluxDBSource(cfg.influx) as source:
        house = source.fetch_series(cfg.entities.house_power, start_ts.to_pydatetime(), end_ts.to_pydatetime(), freq)
        known = {
            load.name: source.fetch_series(load.entity, start_ts.to_pydatetime(), end_ts.to_pydatetime(), freq)
            for load in cfg.entities.known_loads
        }
        temp = (
            source.fetch_series(cfg.entities.outdoor_temp, start_ts.to_pydatetime(), end_ts.to_pydatetime(), freq)
            if cfg.entities.outdoor_temp
            else None
        )
    frame = assemble_power_frame(house=house, known_loads=known, temp=temp, freq=freq)
    return add_derived_columns(frame, cfg.detection.smoothing_window, cfg.detection.baseline_window)


def plot_segment(
    cfg_path: str,
    start: str,
    end: str,
    segment_start: Optional[str] = None,
    segment_end: Optional[str] = None,
    outfile: Optional[str] = None,
) -> None:
    cfg = LoadIQConfig.from_file(Path(cfg_path))
    frame = load_frame(cfg, start, end)

    tz_frame = frame.tz_convert("Europe/Stockholm")

    fig, ax = plt.subplots(figsize=(12, 5), sharex=True)

    ax.plot(tz_frame.index, tz_frame["net_w"], label="Net", color="tab:blue")
    ax.plot(tz_frame.index, tz_frame["net_smoothed_w"], label="Smoothed", color="tab:orange")

    # Draw tolerance band if segment bounds supplied
    if segment_start and segment_end:
        s = pd.Timestamp(segment_start).tz_convert("Europe/Stockholm")
        e = pd.Timestamp(segment_end).tz_convert("Europe/Stockholm")
        segment = tz_frame.loc[s:e]
        if not segment.empty:
            sample_seconds = segment.index.to_series().diff().dt.total_seconds().median()
            if pd.isna(sample_seconds) or sample_seconds <= 0:
                sample_seconds = cfg.entities.house_power.aggregate_every
            ref_count = max(1, min(len(segment), int(max(3, round(60.0 / sample_seconds)))))
            baseline = segment["net_w"].iloc[:ref_count].median()
            tol = max(cfg.detection.spike_tolerance_ratio * baseline, cfg.detection.spike_tolerance_w)
            ax.axhline(baseline + tol, color="tab:red", linestyle="--", linewidth=1, label="Spike threshold")
            ax.axvspan(s, e, color="tab:green", alpha=0.1, label="Segment")

    ax.set_ylabel("Power (W)")
    ax.set_title("LoadIQ Segment Visualizer")
    ax.grid(True, which="both", linestyle="--", alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()

    if outfile:
        fig.savefig(outfile, bbox_inches="tight")
        print(f"Saved figure to {outfile}")
    else:
        plt.show()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Plot LoadIQ segment and spike tolerance")
    parser.add_argument("--config", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--segment-start")
    parser.add_argument("--segment-end")
    parser.add_argument("--output")
    args = parser.parse_args()

    plot_segment(
        cfg_path=args.config,
        start=args.start,
        end=args.end,
        segment_start=args.segment_start,
        segment_end=args.segment_end,
        outfile=args.output,
    )
