# Strategy Mode And Workflow Contract

Strategy Rules displays two separate ideas:

- `platform.strategy_mode_id`: what kind of trading structure the strategy is.
- `platform.workflow_id`: how the current artifact was produced.

Workflow names such as Parameter Matrix and Walk-Forward Analysis must not be stored as strategy modes.

Engine family is a third idea and should stay small:

- `single_asset_engine`: one tradable instrument and one position timeline.
- `multi_asset_portfolio_engine`: multiple tradable instruments, portfolio accounting, selection, allocation, and max holding constraints.

Execution backend is separate from both mode and workflow:

- `vector`
- `non_vector` / sequential event simulation
- `vector_hybrid`: vector precompute for features/signals/selection/target weights, then sequential accounting for cash, costs, turnover, holdings, and rebalance state

Factor pipelines are also separate from mode and workflow. A value/momentum/quality/growth/volatility factor strategy should normally remain `multi_asset_portfolio`, with `strategy_run.factor_pipeline` describing factor construction, preprocessing, composite score, point-in-time audit, cache, and StatAnalyser outputs.

## Strategy Mode

The canonical list lives in:

`backtester/contracts/strategy/mode-registry-v1.json`

The normalized unified run schema lives in:

`backtester/contracts/strategy/strategy-run.schema.json`

The compatibility bridge and execution planner live in:

`backtester/StrategyRunConfig_backtester.py`

Current primary modes:

- `single_asset_signal`: one tradable instrument at a time.
- `multi_factor_entry_exit_roles`: multiple predictive factors with explicit entry/exit roles.
- `calendar_event_session`: date, weekday, session, open, close, holiday, or custom-calendar triggers.
- `multi_asset_portfolio`: multiple assets can be held together up to portfolio limits.

Reserved/planned modes:

- `multi_asset_trigger_selection`: planned event-driven multi-asset selection.
- `dynamic_allocation_rules`: planned rule-driven portfolio weight changes across assets.

Reserved modes must not be emitted as runnable `strategy_run` configs until schema, support checker, runtime, oracle tests, docs, and QuantReview all confirm support.

Pattern win-rate scanning is an `analysis_overlay` only when it is pure diagnostics and does not simulate trades, holdings, capital, or portfolio equity. Once it trades triggered assets or enforces max holdings, it needs an explicitly supported strategy mode and must not be hidden inside filenames or frontend labels.

Multi-factor investing is not a third engine family. It is a vector data and scoring layer that feeds single-asset signals or multi-asset selection:

- Single-asset factor roles use `multi_factor_entry_exit_roles` when factors have explicit entry, exit, filter, or risk roles.
- Cross-sectional factor ranking uses `multi_asset_portfolio` when it selects and holds a portfolio of assets.
- Rule-driven factor allocation is reserved until the runtime path is implemented and reviewed.

Baseline-plus-event allocation is reserved until the runtime path is implemented and reviewed. A future supported method must state:

- `allocation.method = calendar_event_overlay`
- `allocation.baseline_weights`: normal portfolio weights outside event windows.
- `allocation.event_weights`: temporary event-session weights, including shorts when `risk.allow_short = true`.
- `allocation.event`: a calendar node such as `calendar.nth_weekday_of_month`.
- `fill_model.entry_price = open`, `fill_model.exit_price = close`.

Until that path is supported, AI should return `unsupported_needs_new_building_block` rather than creating a runnable config.

Calendar/event rules are a shared trigger layer, not a separate engine family. The same `utils.calendar_events` resolver can produce:

- signal masks for `single_asset_engine`
- trigger sessions and event audit rows for `multi_asset_portfolio_engine` rebalance or selection policies

For example, monthly rotation should use a calendar node such as `calendar.month_start` as the rebalance trigger, then let the portfolio engine handle ranking, max holdings, target weights, and trade execution.

Portfolio rebalance triggers currently include:

- `calendar.every_session`
- `calendar.month_start`
- `calendar.month_end`
- `calendar.quarter_start`
- `calendar.quarter_end`
- `calendar.nth_weekday_of_month`
- `calendar.last_weekday_of_month`
- `calendar.event_date`

The first multi-asset MVP lives at:

`backtester/MultiAssetPortfolioEngine_backtester.py`

The current full example config lives at:

`backtester/contracts/strategy/examples/multi-asset-portfolio-full-config-v1.json`

## Workflow

Current workflow ids:

- `single_backtest`
- `parameter_matrix`
- `walk_forward_analysis`
- `rolling_validation`
- `statanalyser`

The same strategy mode can appear in multiple workflows. For example, the QQQ MA strategy remains `single_asset_signal` in both Metrics and WFA.

`walk_forward_analysis` means rolling IS optimization followed by paired OOS testing of the selected policy. It requires tunable `parameter_domains`. A fixed strategy with no tunable parameters should use `rolling_validation`; it may share the same window runner, but it must not be described as WFA optimization.

## Strategy Rules Sources

The app API builds `strategy_summary` from:

- normalized `strategy_run` when available
- original run or WFA config via `resolved_configs.run_config.config_path`
- `platform.strategy_mode_id` and `platform.workflow_id`
- resolved dataloader config for asset, period, and frequency
- backtester trading params for execution and cost assumptions
- strategy contract for entry, exit, and parameter domains
