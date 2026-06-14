# Repository Structure Notes

This document clarifies the repo layout boundaries that are easiest to confuse
while preparing a public GitHub snapshot.

## Dependency Profiles

- `requirements.txt` is the default Python runtime profile for `python main.py`.
  It covers the FastAPI app and core backtest/WFA/data modules.
- `requirements-dev.txt` includes `requirements.txt` plus test and formatting
  tools. CI installs this profile.
- `requirements-brokers.txt` includes `requirements.txt` plus optional FUTU and
  IBKR adapter packages. Keep this separate because those adapters still require
  local gateway applications, permissions, and market-data entitlements.
- JavaScript dependencies are managed independently by
  `plotter/web/package-lock.json`.

The project currently uses requirements files as the install contract.
`pyproject.toml` stores project metadata and tool configuration, not the full
runtime dependency list.

## App Boundary

- `main.py` starts the browser-first app on `127.0.0.1:2424` by default.
  Use `python main.py --port 2425 --no-browser` when another local app already
  uses the default port.
- `app/api/` is the FastAPI HTTP/WebSocket API and static frontend server.
- `app/runtime/` is the Python run execution and filesystem registry layer
  used by `app.api`.
- `plotter/` is the visualization namespace.
- `plotter/web/` is the current React + Vite frontend source. Its `dist/`
  output is generated and ignored by Git.
- Legacy Python Dash/Plotly plotter modules were removed after the React
  frontend became the supported visualization entrypoint.

## Scripts Boundary

Use `scripts/` for runnable setup, health, and quality helpers. One-off
operator scripts and release-only scratch tools should stay out of the public
repo unless they become part of the supported workflow.

Current scripts:

- `scripts/setup.ps1`
- `scripts/setup.sh`
- `scripts/doctor.py`
- `scripts/workspace_doctor.py`
- `scripts/indicator_doctor.py`
- `scripts/quality_gate.py`
- `scripts/public_release_audit.py`

## Generated Artifacts

Keep runtime outputs and local installation products out of Git:

- `outputs/`
- `logs/`
- `plotter/web/dist/`
- `plotter/web/node_modules/`
- Python caches and test caches
- `verification/baseline_old/`, `verification/candidate_new/`, and
  `verification/results/` generated outputs
