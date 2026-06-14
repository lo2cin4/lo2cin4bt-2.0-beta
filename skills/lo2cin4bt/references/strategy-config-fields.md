# Strategy Config Fields

Preferred user-editable backtest configs use `strategy_run`. WFA configs use `wfa_run` and reference a strategy config instead of duplicating it. For seeded or user-facing workspace runs, put the executable strategy config in `workspace/runs/` and set `strategy_config_path` to an explicit repo-relative path such as `workspace/runs/my-strategy.json`; do not use a bare filename.

## Required Thinking Before Writing Config

Collect:

- Asset/universe.
- Data provider.
- Frequency.
- Calendar and timezone.
- Strategy mode.
- Workflow.
- Entry/exit, selection, or allocation rules.
- Fill timing.
- Costs and slippage.
- Benchmark.
- Risk gates.
- Parameter domains, if any.
- External data availability, if custom data is used.

If any item is ambiguous, ask before writing. If the repo has no implementation evidence for the requested behavior, mark it unsupported.

## Main Sections

| Section | Purpose | Common mistake |
| --- | --- | --- |
| `schema_version` | declares config contract | omitting version on new configs |
| `platform` | `strategy_mode_id`, `workflow_id`, labels | mismatching workflow and parameter domains |
| `data` | provider, frequency, calendar, timezone, benchmark | comparing across incompatible providers |
| `universe` | tradable symbols | using current-only constituents without provenance |
| `computed_fields` | named values computed from market data, such as SMA, EMA, momentum, volatility, ATR, RSI, MACD, z-score, percentile, or Bollinger values | using a computed field before it exists |
| `signals` | entry/exit rules for signal strategies | writing natural language instead of structured fields |
| `selection` | ranking/eligibility for portfolio strategies | ranking on future or unavailable fields |
| `allocation` | weights, top-N, position limits | forgetting short permission or cash behavior |
| `rebalance` | schedule, event, or signal trigger | assuming daily bars can show intraday events without session logic |
| `fill_model` | fill timing, costs, slippage, accounting backend | omitting explicit open/close and delay-bar assumptions |
| `risk` | gates, short permission, exposure limits | treating disabled gates as active |
| `parameter_domains` | matrix/WFA tunable values | using WFA when no parameter exists |
| `outputs` | requested artifacts | expecting pages without required artifacts |

## Strategy Building Block Kinds

Do not treat every Strategy Building Block as an indicator. The authoring layer separates the blocks by `block_kind`:

| `block_kind` | DSL use | Examples |
| --- | --- | --- |
| `indicator` | computed values in `computed_fields` | SMA, EMA, momentum, volatility, ATR, RSI, MACD line/signal/histogram, z-score, percentile, Bollinger |
| `condition_logic` | combine boolean rules in `signals` or `selection` | all, any, not |
| `condition_comparator` | compare fields, constants, or computed values | greater than, less than, equal |
| `cross_condition` | detect crossing between two operands | cross up, cross down |
| `calendar` | create date/session masks or rebalance/event triggers | nth weekday, month start, year end |
| `execution` | mark fill timing or accounting semantics | same-session close, time stop bars |
| `strategy_template` | expand a common strategy shape into config | MA cross, fixed allocation rebalance, momentum rotation |

The Strategy Config DSL is for human/AI authoring. Machine IR is for validators and runtimes. A DSL entry such as `op: indicator.rsi` should compile to the registry canonical id `indicator.rsi` before validation.

## Strategy Modes

- `single_asset_signal`: one asset signal compiled to target weights.
- `calendar_event_session`: same-session calendar/event trade.
- `multi_asset_portfolio`: portfolio selection, fixed allocation, or rotation.
- `multi_factor_entry_exit_roles`: factor-style role boundary; verify current support before use.

Reserved/planned modes such as `dynamic_allocation_rules` and `multi_asset_trigger_selection` must not be emitted as runnable `strategy_run` configs yet. Use `multi_asset_portfolio` plus the currently supported allocation/rebalance blocks when the runtime path is proven; otherwise return an unsupported capability verdict.

## Semantic Indicator Support

For `strategy_run` configs routed through the unified portfolio engine, top-level `computed_fields[]` can define `indicator.sma`, `indicator.ema`, `indicator.momentum`, `indicator.volatility`, `indicator.atr`, `indicator.rsi`, `indicator.macd`, `indicator.zscore`, `indicator.percentile`, and `indicator.bollinger`. After the computed field is named, reuse that name in `selection.rank_by`, `selection.eligible`, or `signals.entry` / `signals.exit`. Use only these canonical `indicator.*` op names; aliases such as `sma`, `ta.sma`, `atr`, or `average_true_range` are intentionally rejected so AI-authored configs stay consistent.

Public indicators are current-bar-completed fields and cannot be used for same-session open entries unless a reviewed implementation proves the signal was known before the fill.

Inline condition feature nodes are no longer part of the public strategy config surface. Define every calculation in `computed_fields[]` first, then reference it by field name.

## Data Availability

Feature contracts for local or external data must include per-feature `data_availability`:

- `observed_at`
- `usable_from`
- `point_in_time`
- `revision_policy`

If the timing or point-in-time status is unknown, ask the user or require QuantReview. Do not assume a value was known before the simulated trade.

## Workflows

- `single_backtest`: one policy.
- `parameter_matrix`: expand `parameter_domains`.
- `walk_forward_analysis`: IS search then OOS selected optimum.
- `rolling_validation`: fixed/no-domain policy across OOS windows.
- `statanalyser`: factor/stat artifact analysis only when generated.

## Parameter Matrix Shape Rules

- If the original user prompt already contains multiple candidate values, numeric ranges, step sizes, or sweep wording, infer `parameter_matrix` automatically and record the inferred domains.
- If the original user prompt names tunable variables but gives no candidate values, ask whether to keep them fixed or test multiple values before drafting the runnable config.
- When the user confirms "sweep all matrix params", keep the sweep inside one `strategy_run` file with `platform.workflow_id = "parameter_matrix"`.
- Do not create one `single_backtest` file per parameter combination.
- `parameter_matrix` must have `parameter_domains` that expand to at least two candidate combinations.
- `single_backtest` must not carry non-empty `parameter_domains`; fixed values belong directly in the strategy logic or a resolved parameter section.

## Provider Notes

- `yfinance`: public ETF/equity data; best beginner route.
- `binance`: crypto symbols such as `BTCUSDT`; keep benchmark provider compatible.
- `coinbase`: product notation such as `BTC-USD` when configured.
- `file`: local CSV/parquet OHLCV.
- `futu` and `ibkr`: require local gateway apps, packages, permissions, and market-data entitlements.

## Validation Before Full Run

- Validate schema.
- Run `python scripts/workspace_doctor.py`.
- Start with one small config.
- Confirm Metrics Overview and Backtests payloads exist.
- Only then run a large Parameter Matrix or WFA.
