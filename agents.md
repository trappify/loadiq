# Agent Handbook

## Mission Snapshot
- **Goal:** estimate appliance-level load (starting with the heat pump) from whole-home power data; later evolve into a Home Assistant integration.
- **Tech Stack:** Python package `loadiq` under `src/`; CLI entry point `loadiq detect`; configuration managed via Pydantic models (`src/loadiq/config.py`) with YAML examples in `config/`.
- **Detectors:** default mode uses adaptive heuristics (`detect_heatpump_segments`) tuned for an on/off compressor (≈2–3 kW, 5–60 min runs, ≥10 min off-time). Legacy “simple” threshold detector remains available via `--mode simple`.

## Key Paths
- `config/local.yaml` – working InfluxDB connection + entity config (contains secrets; treat carefully).
- `data/raw/` – historic CSV pulls from InfluxDB.
- `data/processed/heatpump_segments.csv` – 72 h detection output (start/end/energy for 47 runs).
- `data/processed/heatpump_segments_recent.csv`, `..._current.csv` – ad-hoc slices used during investigation.
- `data/logs/manual_observations.csv` – manual pump status reports logged when the user shares real-world observations (timestamps stored in CET + UTC).
- `tests/` – pytest suite covering config, data access, preprocessing, detectors, and CLI.
- `loadiqctl` – new Click-based CLI (`loadiqctl runs`, `loadiqctl stats`, etc.); enable completion with `eval "$(_LOADIQCTL_COMPLETE=bash_source loadiqctl)"` (or zsh equivalent).

## Running the Pipeline
```bash
# Install (one time)
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]

# Heat-pump detection (adaptive heuristics, 3-day window)
PYTHONPATH=src:. .venv/bin/python -m loadiq.cli.main \
  --config config/local.yaml \
  detect --since 2025-10-28T00:00:00Z \
         --until 2025-10-31T00:00:00Z \
         --mode adaptive \
         --output data/processed/heatpump_segments.csv

# Short-term slice (prints JSON for quick inspection)
PYTHONPATH=src:. .venv/bin/python -m loadiq.cli.main \
  --config config/local.yaml \
  detect --since 2025-10-31T13:00:00Z \
         --until 2025-10-31T16:00:00Z \
         --mode adaptive \
         --json
```

### Detection Parameters (config/local.yaml)
- `min_power_w`: minimum smoothed + raw net load to start a segment (default 2200 W).
- `max_power_w`: segments exceeding this peak are discarded (prevents 6 kW heater cycles from polluting compressor stats).
- `min_duration_s`, `max_duration_s`, `min_off_duration_s`: enforce run/off windows.
- `start_delta_w` / `stop_delta_w`: minimum ∆W for detecting ramp-up/shutdown.
- `baseline_window`, `smoothing_window`: rolling medians/means to track baseline load and smooth noise.

## Manual Observation Log
- Append new entries to `data/logs/manual_observations.csv` whenever the user reports on/off status:
  ```
  timestamp_local,tz,utc_iso,observation
  2025-10-31 16:52,CET,2025-10-31T15:52:00Z,pump running (user observation)
  ```
- Use `printf` or append via Python; keep CET time aligned with local `date` command output.

## Current Findings (Oct 28–30 Run)
- 47 detected runs over 72 h; median duration ≈13 min; mean load ≈2.8 kW; 32.2 kWh total energy (after subtracting the EV load).
- Daily runtime ≈3.3–4.0 h with ~10–18 cycles per day.
- Latest confirmed segment (as of 2025-10-31 15:19 UTC): 12 min, 3.0 kW mean, 0.61 kWh.

## Testing & Quality
- Run `PYTHONPATH=src:. .venv/bin/python -m pytest` after making code changes.
- Tests include adaptive detector behaviour (`tests/test_detection.py`) and CLI invocation via a stubbed data source (`tests/test_cli.py`).

## Notes & Future Hooks
- If the heat pump ever pulls sustained >3.5 kW (e.g., resistance heater cycles), detections are currently dropped; future enhancement could log these separately.
- ML upgrade path: capture labeled on/off intervals (manual log or future telemetry) to train classifiers; infra is ready for additional modules under `src/loadiq/detection/`.
- Home Assistant integration will eventually consume the CLI/module output; keep interfaces decoupled for easy embedding.

## Robustness Roadmap (Current Sensors Only)
1. **Heuristic Hardening**
   - Keep `max_power_w` high enough (currently 5000 W) to avoid discarding valid runs; instead flag segments with `peak_power_w` above ~4 kW for review.
   - Inside each detected segment, compute and store spike metrics (e.g., time spent above 4 kW, largest delta) so we can spot overlaps with other loads even without extra sensors.
   - Improve baseline/residual calculations (longer rolling medians, adaptive smoothing) to better isolate the pump when background loads drift.

2. **Quality Signals & Logging**
   - Continue logging manual observations in `data/logs/manual_observations.csv` and build a script to align them with detector output; use those labels to tune thresholds.
   - Emit per-segment QC fields (e.g., `has_high_spike`, `duty_cycle`, `avg_delta`) in the CLI CSV/JSON so we can review suspect runs quickly.
   - Add optional plotting/notebook utilities to visualise house load vs. detector decisions for any time window.

3. **Automation & Monitoring**
   - Create a daily job (cron/notebook) that runs detection for the last 24 h, compares energy totals against recent averages, and logs anomalies.
   - Extend pytest coverage with synthetic edge cases (short spikes, long idle periods) to guard against regressions when heuristics change.
   - Document any threshold adjustments or new QC metrics back here to keep future work aligned.

Keep this document updated whenever configs, logging conventions, or detection logic change.
