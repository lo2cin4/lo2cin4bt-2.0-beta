# lo2cin4bt 2.0.0 Release Notes

## Release Type

Initial public GitHub baseline for the 2.x line.

## Highlights

- Browser-first local research app served at `http://127.0.0.1:2424/`.
- FastAPI app layer with React/Vite frontend.
- Metrics Overview, Backtests, Parameter Matrix, WFA, Run Center, and AI-readable review pack workflows.
- Strategy contract path for semantic strategy configs.
- Portfolio accounting, rebalance audit, cost/slippage diagnostics, and provenance-aware payloads.
- Repo-local Codex skill at `skills/lo2cin4bt/SKILL.md` for beginner setup, AI usage, strategy writing, frontend interpretation, troubleshooting, and quant-risk boundaries.

## Public GitHub Boundary

- Runtime outputs under `outputs/` are not included.
- Local workspace configs and datasets are not included by default.
- Stable tests, contracts, docs, fixtures, setup scripts, bundled frontend font files, and the repo-local skill are included.
- FUTU / IBKR packages remain optional market-data gateway support only; this release does not enable live trading or order placement.

## Upgrade Notes

- Old module-specific output folders and `records/` are not the active public app contract.
- If an old run lacks current payload fields, delete it from the validation set and rerun with the 2.0.0 code.
- On first app launch, included examples are copied from `backtester/contracts/strategy/examples/` into ignored local workspace folders so Run Center shows runnable batches without tracking user workspace configs in Git.
