# lo2cin4bt AI Manual

This file is the long-form operating manual for AI assistants working with lo2cin4bt. The canonical repo-local Codex skill remains `skills/lo2cin4bt/SKILL.md`; use that first, then load references under `skills/lo2cin4bt/references/` as needed.

## Core Rule

AI may help turn a strategy idea into validated local research artifacts, but it must not invent unsupported engine behavior.

Normal strategy work belongs in `workspace/`:

- `workspace/datasets/`: local CSV, Excel, or Parquet data.
- `workspace/features/`: feature contracts for external data.
- `workspace/indicators/extensions/`: custom indicator packages.
- `workspace/runs/`: runnable `strategy_run` configs.
- `workspace/wfa/`: `wfa_run` configs.
- `workspace/reports/`: local AI notes and review records.

Do not modify core folders such as `backtester/`, `app/`, `dataloader/`, `autorunner/`, `plotter/`, `metricstracker/`, or `wfanalyser/` during normal strategy authoring. If a user request requires engine behavior that is not supported, return an unsupported capability verdict and propose an engine feature request.

Workspace sharing follows the existing folder design. A shared strategy is reusable only when every referenced file is present at the same workspace path, validation passes, and the runtime supports the requested behavior. Custom indicator folders under `workspace/indicators/extensions/` are code packages, not automatic formulas. They require `manifest.json`, `indicator.py`, `indicator_doctor`, and runtime dispatch support. If the package validates but runtime dispatch is missing, return `unsupported_needs_new_building_block`.

## Required Checks Before Writing A Config

Before drafting a runnable config, collect:

- asset universe and benchmark;
- data provider, frequency, calendar, and timezone;
- strategy mode and workflow;
- entry, exit, selection, allocation, or rebalance rules;
- `fill_model` with entry/exit price, delay bars, cost, and slippage;
- parameter domains, if the user wants a sweep;
- risk gates and short permissions, if relevant;
- external data availability and point-in-time assumptions.

If any item is unclear, ask. If the engine lacks support, stop.

## Current Public Config Surface

Preferred user-editable configs use:

- `schema_version: "strategy_run"` for backtests;
- `schema_version: "wfa_run"` for WFA;
- `computed_fields[]` for calculated values such as SMA, EMA, RSI, momentum, ATR, volatility, z-score, percentile, Bollinger, or MACD;
- `signals`, `selection`, `allocation`, and `rebalance` for strategy logic;
- `fill_model` for execution price, delay bars, cost, slippage, and accounting backend.

Do not create new public configs with top-level `features`, top-level `indicators`, or `execution` sections. Those names may appear in older notes or internal history, but new strategy authoring should use `computed_fields` and `fill_model`.

Reserved or planned modes such as `dynamic_allocation_rules` and `multi_asset_trigger_selection` must not be emitted as runnable configs unless schema, support checker, runtime path, oracle tests, docs, and QuantReview all confirm support. Otherwise return `unsupported_needs_new_building_block`.

## Parameter Matrix Rule

If the user gives multiple values, ranges, step sizes, or wording such as "sweep", create one `strategy_run` file with `platform.workflow_id = "parameter_matrix"` and all values inside `parameter_domains`.

Do not create one `single_backtest` per parameter combination.

If the user names tunable variables but does not provide values, ask whether to keep them fixed or test multiple candidates.

## Data Availability Rule

Every feature contract under `workspace/features/` must declare `data_availability` for each feature:

- `observed_at`: when the value first becomes observable;
- `usable_from`: earliest simulated trading time allowed to use it;
- `point_in_time`: whether the source preserves the value known at that time;
- `revision_policy`: `point_in_time`, `declared_static`, or `revised_history`.

AI may fill these fields only when the timing is explicit. If timing, publication lag, or point-in-time status is unknown, ask the user or mark the data as requiring QuantReview.

Examples:

- A value known at bar close cannot drive a same-bar open trade.
- A value known only after bar close cannot drive any same-bar trade.
- Revised history is allowed only with clear disclosure and QuantReview.

## Validation Before Running

Run these before full backtests:

```powershell
python scripts/workspace_doctor.py --config workspace/runs/your-config.json
python scripts/indicator_doctor.py workspace/indicators/extensions
python scripts/project_consistency_audit.py
```

Use a small run before a large Parameter Matrix or WFA.

## Result Review

When reviewing results, read:

- run registry;
- chart payloads;
- artifact manifest;
- run snapshot;
- data lineage manifest;
- AI review pack if generated.

Do not treat the best Parameter Matrix row as out-of-sample proof. WFA is stronger than a single in-sample grid, but still not a guarantee of future performance.

## Safety Boundary

lo2cin4bt is for local research and backtesting. It currently does not support order placement, broker orders, fund movement, position changes, or account-setting changes. Broker or exchange integrations are read-only market-data routes.
