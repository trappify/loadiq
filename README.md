# LoadIQ

LoadIQ is a modular toolkit for analysing household energy consumption and isolating appliance-level loads without direct sub-metering. The project is designed to evolve from quick data explorations to production-ready integrations (e.g. Home Assistant) by keeping ingestion, preprocessing, and detection concerns separate.

## Project Objectives
- Fetch and align high-resolution aggregate power data alongside contextual signals (e.g. weather, known loads).
- Provide a reusable preprocessing pipeline that can support multiple NILM experiments.
- Implement adaptive load-detection strategies (delta + duration heuristics today, ML-ready later).
- Offer a CLI for offline analysis while targeting an eventual Home Assistant integration.

## Repository Layout
```
.
├── data/               # Raw and processed datasets (gitignored)
├── notebooks/          # Exploratory analysis notebooks
├── src/
│   └── loadiq/
│       ├── data/       # Data access abstractions and Influx integration
│       ├── preprocessing/  # Resampling, alignment, feature engineering
│       ├── detection/  # Load segmentation and estimation logic
│       └── cli/        # Command-line entry points
└── tests/              # Unit and integration tests
```

## Getting Started
1. Create a virtual environment and install the package in editable mode:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .[viz,dev]
   ```
2. Configure environment variables for InfluxDB access (see below) or point the CLI at a configuration file.
3. Run the CLI to fetch data and produce a detection report:
   ```bash
   loadiq detect --since 2025-10-28T00:00Z --until 2025-10-31T00:00Z
   ```

### New Interactive CLI (`loadiqctl`)

Install editable deps (as above) and use the richer control CLI:

```bash
# Show recent runs (default 3 hours). The ./loadiq wrapper is equivalent to loadiqctl.
./loadiq runs last-1h

# Quick window expressions (the WINDOW argument is optional)
loadiqctl runs last-1h                # last hour
loadiqctl runs -6h..-3h               # relative range
loadiqctl runs 2024-02-01..2024-02-02 # absolute range

# Each table now includes a "Total" row summarizing runtime and energy.

# Detect a custom window and write CSV
loadiqctl detect 2025-10-31T00:00Z..2025-10-31T12:00Z --output data/report.csv

# View 30-day stats (raw/clamped energy, durations, temps)
loadiqctl stats --days 30
```

Relative shorthands work both in the WINDOW argument and the advanced flags (`--since`, `--until`, `--window`) if you need fine-grained control.

Tab completion is available via Click. Example for Bash (add to `.bashrc`):

```bash
eval "$(_LOADIQCTL_COMPLETE=bash_source loadiqctl)"
```

For Zsh:

```bash
eval "$(_LOADIQCTL_COMPLETE=zsh_source loadiqctl)"
```

Run `loadiqctl --help` for command summaries.

## Configuration
LoadIQ expects connection and entity details either via CLI options or a YAML/JSON configuration file. The CLI discovers the configuration in this order:

- `--config PATH` if provided
- `LOADIQ_CONFIG` environment variable
- `config/local.yaml` (current directory or project root)
- `config/example.yaml`
- `~/.config/loadiq/config.yaml` or `~/.loadiq/config.yaml`
- Falling back to `LOADIQ_*` environment variables (`LOADIQ_INFLUX_URL`, `LOADIQ_INFLUX_TOKEN`, `LOADIQ_INFLUX_ORG`, `LOADIQ_INFLUX_BUCKET`, `LOADIQ_HOUSE_ENTITY`, optional known load / outdoor IDs)

The core fields are:
- `influx.url`, `influx.token`, `influx.org`, `influx.bucket`
- `entities.house_power`, `entities.outdoor_temp`, `entities.known_loads` (list of sensors like EV chargers)
- `detection.*` entries (power thresholds, duration/off windows, delta triggers)
  - `spike_tolerance_ratio` / `spike_tolerance_w` / `spike_min_duration_s` control how sensitive the detector is to short spikes within a run.

See `src/loadiq/config.py` for the full schema.

## Roadmap
- [ ] Implement richer feature engineering and ML-based heat pump classification.
- [ ] Build automated tests around data alignment and detection segments.
- [ ] Package the detection output as a Home Assistant custom component.
- [ ] Support additional data sources (MQTT, file-based replay) via the `PowerDataSource` interface.

## License
MIT © trappify
