"""Rule-based load detection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, List, Optional

import pandas as pd

from ..config import DetectionConfig


@dataclass
class DetectedSegment:
    """Represents a contiguous period where a target load is active."""

    start: pd.Timestamp
    end: pd.Timestamp
    duration: timedelta
    mean_power_w: float
    peak_power_w: float
    energy_kwh: float
    temperature_c: Optional[float]
    spike_energy_kwh: float = 0.0
    has_spike: bool = False
    clamped_energy_kwh: float = 0.0

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_minutes": self.duration.total_seconds() / 60,
            "mean_power_w": self.mean_power_w,
            "peak_power_w": self.peak_power_w,
            "energy_kwh": self.energy_kwh,
            "temperature_c": self.temperature_c,
            "spike_energy_kwh": self.spike_energy_kwh,
            "has_spike": self.has_spike,
            "clamped_energy_kwh": self.clamped_energy_kwh,
        }


def _estimate_sample_seconds(index: pd.DatetimeIndex) -> float:
    diffs = index.to_series().diff().dropna()
    if diffs.empty:
        return 0.0
    return diffs.dt.total_seconds().median()


def _compute_spike_metrics(
    window: pd.DataFrame,
    reference_power: float,
    tolerance_w: float,
    sample_seconds: float,
    min_duration_s: float,
) -> tuple[float, bool]:
    if window.empty or sample_seconds <= 0.0:
        return 0.0, False

    threshold = reference_power + tolerance_w
    values = window["net_w"].to_numpy()
    above = values > threshold

    energy_factor = sample_seconds / 3600.0
    spike_energy = 0.0
    has_spike = False
    current_indices: List[int] = []

    def flush(indices: List[int]) -> None:
        nonlocal spike_energy, has_spike
        if not indices:
            return
        duration = len(indices) * sample_seconds
        if duration >= min_duration_s:
            has_spike = True
            excess = 0.0
            for idx in indices:
                excess += max(0.0, values[idx] - threshold)
            spike_energy += excess * energy_factor / 1000.0

    for idx, is_above in enumerate(above):
        if is_above:
            current_indices.append(idx)
        else:
            flush(current_indices)
            current_indices = []

    flush(current_indices)

    return float(spike_energy), has_spike


def detect_segments(
    df: pd.DataFrame,
    min_power_w: float,
    min_duration_s: float,
    use_column: str = "net_smoothed_w",
) -> List[DetectedSegment]:
    """Detect segments where the smoothed net power exceeds the configured threshold."""

    if use_column not in df.columns:
        raise ValueError(f"Missing column '{use_column}' in dataframe")

    active = df[use_column] >= min_power_w
    segments: List[DetectedSegment] = []
    start_ts: Optional[pd.Timestamp] = None

    sample_seconds = _estimate_sample_seconds(df.index)
    energy_factor = sample_seconds / 3600.0  # kWh = watts * hours

    for ts, is_active in active.items():
        if is_active and start_ts is None:
            start_ts = ts
        elif not is_active and start_ts is not None:
            end_ts = ts
            window = df.loc[start_ts:end_ts]
            duration = (end_ts - start_ts).total_seconds()
            if duration >= min_duration_s and not window.empty:
                energy = (window["net_w"].sum() * energy_factor) / 1000 if sample_seconds else 0.0
                temp_mean = window.get("outdoor_temp_c").mean() if "outdoor_temp_c" in window else None
                segments.append(
                    DetectedSegment(
                        start=start_ts,
                        end=end_ts,
                        duration=timedelta(seconds=duration),
                        mean_power_w=float(window["net_w"].mean()),
                        peak_power_w=float(window["net_w"].max()),
                        energy_kwh=float(energy),
                        temperature_c=float(temp_mean) if temp_mean is not None else None,
                    )
                )
            start_ts = None

    if start_ts is not None:
        end_ts = df.index[-1]
        window = df.loc[start_ts:end_ts]
        duration = (end_ts - start_ts).total_seconds()
        if duration >= min_duration_s and not window.empty:
            energy = (window["net_w"].sum() * energy_factor) / 1000 if sample_seconds else 0.0
            temp_mean = window.get("outdoor_temp_c").mean() if "outdoor_temp_c" in window else None
            segments.append(
                DetectedSegment(
                    start=start_ts,
                    end=end_ts,
                    duration=timedelta(seconds=duration),
                    mean_power_w=float(window["net_w"].mean()),
                    peak_power_w=float(window["net_w"].max()),
                    energy_kwh=float(energy),
                    temperature_c=float(temp_mean) if temp_mean is not None else None,
                )
            )

    return segments


def detect_heatpump_segments(
    df: pd.DataFrame,
    config: DetectionConfig,
    use_column: str = "net_smoothed_w",
) -> List[DetectedSegment]:
    """Adaptive detector tailored for on/off heat pumps."""

    if use_column not in df.columns:
        raise ValueError(f"Missing column '{use_column}' in dataframe")

    if "net_diff_w" not in df.columns:
        raise ValueError("Dataframe missing 'net_diff_w' column; run add_derived_columns first")

    min_power = config.min_power_w
    max_power = config.max_power_w
    min_duration = config.min_duration_s
    max_duration = config.max_duration_s
    min_off = config.min_off_duration_s
    start_delta = config.start_delta_w
    stop_delta = config.stop_delta_w

    sample_seconds = _estimate_sample_seconds(df.index)
    energy_factor = sample_seconds / 3600.0 if sample_seconds else 0.0

    segments: List[DetectedSegment] = []
    in_segment = False
    segment_start: Optional[pd.Timestamp] = None
    peak_power = 0.0
    last_end: Optional[pd.Timestamp] = None

    for ts, row in df.iterrows():
        smoothed_power = float(row[use_column])
        raw_power = float(row.get("net_w", smoothed_power))
        delta = float(row.get("net_diff_w", 0.0))
        baseline = float(row.get("net_baseline_w", 0.0))

        if not in_segment:
            if raw_power >= min_power and (delta >= start_delta or smoothed_power >= min_power):
                if last_end is None or (ts - last_end).total_seconds() >= min_off:
                    in_segment = True
                    segment_start = ts
                    peak_power = raw_power
            continue

        # we're inside a segment
        if segment_start is None:
            in_segment = False
            continue

        peak_power = max(peak_power, raw_power)
        duration = (ts - segment_start).total_seconds()

        stop_due_to_drop = delta <= -stop_delta and raw_power <= min_power * 0.8
        stop_due_to_low = raw_power <= min_power * 0.5 and smoothed_power <= min_power * 0.6
        stop_due_to_baseline = baseline <= min_power * 0.6 and raw_power <= baseline + min_power * 0.2
        should_close = stop_due_to_drop or stop_due_to_low or stop_due_to_baseline or duration >= max_duration

        if not should_close:
            continue

        window = df.loc[segment_start:ts]
        duration = (ts - segment_start).total_seconds()
        if duration < min_duration or peak_power > max_power or window.empty:
            in_segment = False
            segment_start = None
            peak_power = 0.0
            continue

        energy_kwh = 0.0
        if energy_factor:
            energy_kwh = float(window["net_w"].sum() * energy_factor / 1000.0)
        if sample_seconds > 0.0:
            ref_sample_count = max(1, min(len(window), int(max(3, round(60.0 / sample_seconds)))))
        else:
            ref_sample_count = len(window)
        reference_power = float(window["net_w"].iloc[:ref_sample_count].median()) if not window.empty else 0.0
        tolerance = max(config.spike_tolerance_ratio * reference_power, config.spike_tolerance_w)
        spike_energy_kwh, has_spike = _compute_spike_metrics(
            window,
            reference_power,
            tolerance,
            sample_seconds,
            config.spike_min_duration_s,
        )
        clamped_series = window["net_w"].clip(upper=reference_power + tolerance)
        clamped_energy_kwh = 0.0
        if energy_factor:
            clamped_energy_kwh = float(clamped_series.sum() * energy_factor / 1000.0)
        temp_mean = window.get("outdoor_temp_c").mean() if "outdoor_temp_c" in window else None

        segments.append(
            DetectedSegment(
                start=segment_start,
                end=ts,
                duration=pd.Timedelta(seconds=duration),
                mean_power_w=float(window["net_w"].mean()),
                peak_power_w=float(window["net_w"].max()),
                energy_kwh=energy_kwh,
                temperature_c=float(temp_mean) if temp_mean is not None else None,
                spike_energy_kwh=spike_energy_kwh,
                has_spike=has_spike,
                clamped_energy_kwh=clamped_energy_kwh,
            )
        )

        in_segment = False
        segment_start = None
        peak_power = 0.0
        last_end = ts

    # Handle segment that runs until the end
    if in_segment and segment_start is not None:
        end_ts = df.index[-1]
        window = df.loc[segment_start:end_ts]
        duration = (end_ts - segment_start).total_seconds()
        if duration >= min_duration and duration <= max_duration and window["net_w"].max() <= max_power:
            energy_kwh = 0.0
            if energy_factor:
                energy_kwh = float(window["net_w"].sum() * energy_factor / 1000.0)
            if sample_seconds > 0.0:
                ref_sample_count = max(1, min(len(window), int(max(3, round(60.0 / sample_seconds)))))
            else:
                ref_sample_count = len(window)
            reference_power = float(window["net_w"].iloc[:ref_sample_count].median()) if not window.empty else 0.0
            tolerance = max(config.spike_tolerance_ratio * reference_power, config.spike_tolerance_w)
            spike_energy_kwh, has_spike = _compute_spike_metrics(
                window,
                reference_power,
                tolerance,
                sample_seconds,
                config.spike_min_duration_s,
            )
            clamped_series = window["net_w"].clip(upper=reference_power + tolerance)
            clamped_energy_kwh = 0.0
            if energy_factor:
                clamped_energy_kwh = float(clamped_series.sum() * energy_factor / 1000.0)
            temp_mean = window.get("outdoor_temp_c").mean() if "outdoor_temp_c" in window else None

            segments.append(
                DetectedSegment(
                    start=segment_start,
                    end=end_ts,
                    duration=pd.Timedelta(seconds=duration),
                    mean_power_w=float(window["net_w"].mean()),
                    peak_power_w=float(window["net_w"].max()),
                    energy_kwh=energy_kwh,
                    temperature_c=float(temp_mean) if temp_mean is not None else None,
                    spike_energy_kwh=spike_energy_kwh,
                    has_spike=has_spike,
                    clamped_energy_kwh=clamped_energy_kwh,
                )
            )

    return segments
