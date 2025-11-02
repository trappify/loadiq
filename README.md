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

## Home Assistant Integration (preview)
- Enable the development stack with `scripts/ha up` (runs on http://localhost:8125 with `loadiq` / `loadiq`). Use `scripts/ha down` to stop, or `scripts/ha reset` if you explicitly want a fresh config.
- The custom component lives in `custom_components/loadiq` and is already wired for HACS (see `hacs.json`). The config flow lets you pick between direct InfluxDB access and native Home Assistant sensors, with an options flow for edits.
- Tests covering the config flow live in `tests/test_integration_config_flow.py`. Run the full suite with `.venv/bin/pytest` after making changes.
- HACS is downloaded automatically during `scripts/ha up`/`reset`; finish setup via Settings → Devices & Services → “+ Add Integration” → HACS. The integration ships the LoadIQ library in the component itself so no extra pip install is needed.
- The dev stack now vendors the `remote_homeassistant` integration. To mirror real sensors into the sandbox, uncomment the `remote_homeassistant:` include in `ha_dev/configuration.yaml`, then edit `ha_dev/config/remote_homeassistant.yaml` and replace the empty `instances: []` with your remote host/access token. You can reference secrets via `!secret` (define them in `ha_dev/config/secrets.yaml`; see `secrets.example.yaml` for placeholders). Include only the entities you need, e.g. `sensor.total_power` or `sensor.ev_charger`, and they will show up in the dev UI and recorder for LoadIQ.
- Real-time detection now exposes a confidence score and keeps a rolling list of classified runs via the `sensor.loadiq_recent_runs` entity. When the heuristics mislabel a run, call the `loadiq.mark_segment` service (Developer Tools → Actions) to tag the segment as `heatpump` or `other`, or use the one-click helpers `loadiq.mark_current_active` / `loadiq.mark_current_inactive` while a run is flagged. LoadIQ persists the feedback and adapts future classifications automatically.
- New in the integration: the binary sensor now reports a confidence flag (“pending” vs “confirmed”), and a `sensor.loadiq_recent_runs` entity lists detected runs in a configurable rolling window (defaults to 3 h). Adjust the window via the “Recent runs window (hours)” field in the config/option flow if you want a longer or shorter summary.

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

### Training the detector

- The Home Assistant integration surfaces detected runs with classification confidence. Use the `loadiq.mark_segment` service (Developer Tools → Actions) or the quick `loadiq.mark_current_active` / `loadiq.mark_current_inactive` helpers to label a run (heat pump vs other) straight from Home Assistant – LoadIQ stores the feedback and updates future classifications without editing any files.
- `sensor.loadiq_recent_runs` lists the runs inside your configured window and mirrors the same confidence information so you can review what the detector saw during the last few hours.

## Roadmap
- [ ] Implement richer feature engineering and ML-based heat pump classification.
- [ ] Build automated tests around data alignment and detection segments.
- [ ] Package the detection output as a Home Assistant custom component.
- [ ] Support additional data sources (MQTT, file-based replay) via the `PowerDataSource` interface.

## License
MIT © trappify
