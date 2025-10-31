import pandas as pd
import pytest

from loadiq.preprocessing.align import add_derived_columns, assemble_power_frame


def _make_series(values, start="2025-01-01T00:00:00Z", freq="10s"):
    idx = pd.date_range(start=start, periods=len(values), freq=freq, tz="UTC")
    return pd.DataFrame({"value": values}, index=idx)


def test_assemble_power_frame_basic():
    house = _make_series([1000, 1200, 1300, 1100])
    known = {"ev": _make_series([0, 200, 0, 0])}
    temp = _make_series([5.0, 4.8, 4.6, 4.5], freq="60s")

    df = assemble_power_frame(house=house, known_loads=known, temp=temp, freq="10s")

    assert "house_w" in df
    assert "load_ev_w" in df
    assert pytest.approx(df["net_w"].iloc[1]) == 1000


def test_assemble_power_frame_requires_house():
    with pytest.raises(ValueError):
        assemble_power_frame(house=pd.DataFrame(columns=["value"]), known_loads={}, temp=None)


def test_add_derived_columns():
    house = _make_series([1000, 1500, 1000, 1500])
    df = assemble_power_frame(house=house, known_loads={}, temp=None, freq="10s")

    enriched = add_derived_columns(df, smoothing_window=2, baseline_window=3)
    assert "net_smoothed_w" in enriched
    assert "net_diff_w" in enriched
    assert "net_baseline_w" in enriched
    assert "net_above_baseline_w" in enriched
    assert pytest.approx(enriched["net_smoothed_w"].iloc[1]) == 1250
