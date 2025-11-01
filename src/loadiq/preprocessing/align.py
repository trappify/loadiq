"""Utilities for resampling and aligning time-series data."""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

import pandas as pd


def _resample_series(series: pd.Series, freq: str) -> pd.Series:
    series = series.sort_index()
    series = series[~series.index.duplicated(keep="first")]
    return series.resample(freq).mean()


def assemble_power_frame(
    house: pd.DataFrame,
    known_loads: Dict[str, pd.DataFrame],
    temp: pd.DataFrame | None,
    freq: str = "10s",
    interpolation_limit: int = 36,
) -> pd.DataFrame:
    """
    Build a unified dataframe with house load, known loads, outdoor temperature, and net load.
    """

    if house.empty:
        raise ValueError("House power dataframe is empty; confirm the query parameters.")

    frame = pd.DataFrame()
    frame["house_w"] = _resample_series(house["value"], freq)

    for name, df in known_loads.items():
        series = _resample_series(df["value"], freq)
        frame[f"load_{name}_w"] = series.fillna(0.0)

    if temp is not None and not temp.empty:
        frame["outdoor_temp_c"] = _resample_series(temp["value"], freq).ffill(limit=180)

    frame = frame.infer_objects(copy=False)
    frame = frame.interpolate(limit=interpolation_limit)
    frame = frame.ffill()
    frame = frame.infer_objects(copy=False)
    frame = frame.dropna(subset=["house_w"])

    load_columns = [col for col in frame.columns if col.startswith("load_")]
    if load_columns:
        frame["known_loads_w"] = frame[load_columns].sum(axis=1)
    else:
        frame["known_loads_w"] = 0.0
    frame["net_w"] = frame["house_w"] - frame["known_loads_w"]

    return frame


def add_derived_columns(
    df: pd.DataFrame,
    smoothing_window: int = 6,
    baseline_window: int = 180,
) -> pd.DataFrame:
    """
    Augment the dataframe with smoothed and differential features used for detection.
    """

    result = df.copy()
    result["net_smoothed_w"] = result["net_w"].rolling(window=smoothing_window, min_periods=1).mean()
    result["net_diff_w"] = result["net_w"].diff()
    result["net_diff_abs_w"] = result["net_diff_w"].abs()
    baseline = result["net_w"].rolling(window=baseline_window, min_periods=1).median()
    result["net_baseline_w"] = baseline
    result["net_above_baseline_w"] = result["net_w"] - baseline
    dt_seconds = result.index.to_series().diff().dt.total_seconds()
    median_dt = dt_seconds.median()
    result["sample_interval_s"] = dt_seconds.fillna(median_dt if pd.notna(median_dt) else 0.0)
    denom = result["sample_interval_s"].replace(0, pd.NA)
    diff_per_s = result["net_diff_w"] / denom
    result["net_diff_per_s"] = diff_per_s.replace([float("inf"), float("-inf")], pd.NA)
    return result
