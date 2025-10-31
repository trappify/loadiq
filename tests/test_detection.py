from datetime import timedelta

import pandas as pd
import pytest

from loadiq.config import DetectionConfig
from loadiq.detection.segments import detect_heatpump_segments, detect_segments
from loadiq.preprocessing.align import add_derived_columns


def test_detect_segments_simple():
    idx = pd.date_range("2025-01-01T00:00:00Z", periods=12, freq="10s", tz="UTC")
    net = [500] * 3 + [2500] * 6 + [600] * 3
    frame = pd.DataFrame({"net_w": net}, index=idx)
    frame = add_derived_columns(frame, smoothing_window=2, baseline_window=3)

    segments = detect_segments(frame, min_power_w=2000, min_duration_s=50)
    assert len(segments) == 1
    seg = segments[0]
    assert seg.duration >= timedelta(seconds=50)
    assert seg.mean_power_w > 2000
    assert seg.energy_kwh > 0
    assert not seg.has_spike


def test_detect_heatpump_segments_adaptive():
    idx = pd.date_range("2025-01-01T00:00:00Z", periods=80, freq="10s", tz="UTC")
    net = []
    for i in range(len(idx)):
        if 10 <= i < 30:
            net.append(2600 + 100 * ((i - 10) % 2))
        elif 40 <= i < 45:
            net.append(2300)
        else:
            net.append(500 + 20 * ((i // 5) % 2))
    frame = pd.DataFrame({"net_w": net}, index=idx)
    enriched = add_derived_columns(frame, smoothing_window=3, baseline_window=9)

    cfg = DetectionConfig(
        min_power_w=1800,
        max_power_w=3300,
        min_duration_s=120,
        max_duration_s=1800,
        min_off_duration_s=120,
        smoothing_window=3,
        baseline_window=9,
        start_delta_w=500,
        stop_delta_w=500,
    )

    segments = detect_heatpump_segments(enriched, cfg)
    assert len(segments) == 1
    seg = segments[0]
    assert seg.duration.total_seconds() >= 120
    assert seg.mean_power_w > 2000
    assert seg.peak_power_w <= cfg.max_power_w
    assert seg.energy_kwh > 0
    assert not seg.has_spike


def test_detect_heatpump_segments_spike_flag():
    idx = pd.date_range("2025-01-01T00:00:00Z", periods=60, freq="10s", tz="UTC")
    net = []
    for i in range(len(idx)):
        if i < 6:
            net.append(600)
        elif 6 <= i < 30:
            base = 2800
            if 18 <= i < 21:
                base += 1200  # spike for 30 seconds
            net.append(base)
        else:
            net.append(700)

    frame = pd.DataFrame({"net_w": net}, index=idx)
    enriched = add_derived_columns(frame, smoothing_window=3, baseline_window=9)

    cfg = DetectionConfig(
        min_power_w=1800,
        max_power_w=5000,
        min_duration_s=200,
        max_duration_s=1800,
        min_off_duration_s=60,
        smoothing_window=3,
        baseline_window=9,
        start_delta_w=400,
        stop_delta_w=400,
        spike_tolerance_ratio=0.2,
        spike_tolerance_w=300,
        spike_min_duration_s=20,
    )

    segments = detect_heatpump_segments(enriched, cfg)
    assert len(segments) == 1
    seg = segments[0]
    assert seg.has_spike is True
    assert seg.spike_energy_kwh > 0
    assert seg.clamped_energy_kwh < seg.energy_kwh
