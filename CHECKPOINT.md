# LoadIQ Project Checkpoint (2025-10-31)

## Repo State
- Latest commit: commits include adaptive detection with spike trimming, garage heat-pump subtraction, CLI spike notes, plotting helper, clamped energy/peak metrics.
- Virtual env (.venv) used for running CLI/tests.

## Key Files
- `config/local.yaml` – Influx credentials and entities (`power_norderangsvagen_32`, `nerd_juicer_power_w`, `hppm_g_em0_power`, outdoor temperature).
- `data/logs/manual_observations.csv` – manual run/stopped timestamps in CET.
- `data/processed/heatpump_segments_3h.csv` – most recent 3h detection output.
- `notebooks/segment_visualizer.py` – plotting helper for segments + spike threshold.
- `agents.md` – roadmap + workflow instructions.

## Detector Configuration Highlights
- Adaptive mode with rolling baseline, start/stop deltas, spike detection.
- Spike tolerance: ratio 0.25, absolute 400 W, min duration 30s.
- `max_power_w` currently 5000 W.
- Garage heat pump treated as subtractive known load.

## Recent Findings (30 days ending 2025-10-31)
- Total runs: 594 (≈19.2/day); mean duration 14.6 min, p95 24.3 min.
- Total energy: 415.9 kWh raw vs 411.9 kWh clamped.
- Spike overlaps: 92 runs (~15.5%), spike energy sum 3.87 kWh.
- Weak negative correlation between duration and mean outdoor temperature (r ≈ -0.17).
- Temperature band stats recorded (see CLI output or `agents.md`).

## Commands
- Run detection: `PYTHONPATH=src:. .venv/bin/python -m loadiq.cli.main --config config/local.yaml detect --since <start> --until <end> --mode adaptive`
- Plot window: `PYTHONPATH=src:. .venv/bin/python notebooks/segment_visualizer.py --config config/local.yaml --start <iso> --end <iso> --segment-start <iso> --segment-end <iso> --output plot.png`
- Tests: `PYTHONPATH=src:. .venv/bin/python -m pytest`

## Next Ideas
- Fine-tune spike tolerance or subtract spike energy from totals automatically.
- Plot duration vs temperature; monitor anomalies (e.g., Oct 8 long cycles at warm temps).
- Capture more manual observations for validation / future ML labeling.
