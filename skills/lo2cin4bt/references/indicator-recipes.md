# Indicator Recipes

Use these as beginner-safe patterns. Always inspect the actual config before running. In beginner-facing text, call repo-supported strategy parts **Strategy Building Blocks**.

## Strategy Building Blocks

lo2cin4bt 2.0 uses Strategy Building Blocks instead of one hard-coded template per indicator. A strategy should be assembled from:

- `computed_fields[]`: named values computed from market data, such as SMA, EMA, momentum, volatility, ATR, RSI, MACD, z-score, percentile, or Bollinger values.
- `signals.entry` / `signals.exit`: entry and exit conditions using fields, right-side fields, comparators, crosses, `all`, `any`, and `not`.
- `selection`: eligibility filters, ranking fields, ranking order, and top-N choice.
- `allocation`, `rebalance`, `fill_model`, and `risk`: portfolio weights, schedule, fill timing, costs, slippage, and limits.

Use the two-layer contract in `backtester/contracts/strategy_authoring/strategy-authoring-layers-v1.json`: author strategies in the Strategy Config DSL, then compile/validate them through Machine IR and the registry before runtime execution. Calculated values belong in `computed_fields`; logic/comparators/crosses belong in `signals` or `selection`; calendar blocks belong in `signals` or `rebalance`; templates are authoring scaffolds only.

Current indicator Strategy Building Blocks are available through top-level `computed_fields[]`: define `indicator.sma`, `indicator.ema`, `indicator.momentum`, `indicator.volatility`, `indicator.atr`, `indicator.rsi`, `indicator.macd`, `indicator.zscore`, `indicator.percentile`, or `indicator.bollinger` first, then reference the computed-field name from `selection.rank_by`, `selection.eligible`, or signal conditions. Use only these canonical `indicator.*` op names; do not write aliases such as `sma`, `ta.sma`, `atr`, or `average_true_range`. For MACD, use one op only: `indicator.macd` with `output` set to `line`, `signal`, or `histogram`. Do not document arbitrary formulas inside signal rules until runtime tests cover them.

`indicator.atr` requires OHLC data: by default it reads `high`, `low`, and `close`, or explicit `high_source`, `low_source`, and `close_source`. Public indicators are current-bar-completed fields, so they must not trigger an entry at the same bar open. Same-session strategies should use calendar or other pre-known entry rules, then close at the same session close.

Custom user indicators stay in the existing workspace design: `workspace/indicators/extensions/<slug>/manifest.json` plus `indicator.py`. They are not automatically runnable just because the folder exists. Require `indicator_doctor`, runtime dispatch support, and a small smoke backtest. If any part is missing, return `unsupported_needs_new_building_block`.

## Single Asset MA Cross / Parameter Matrix

Goal: test a symbol such as `QQQ` with short/long moving-average crossover parameters.

Expected config shape:

- `platform.strategy_mode_id = "single_asset_signal"`
- `platform.workflow_id = "parameter_matrix"` when sweeping parameters.
- `data.provider = "yfinance"` for public ETF examples.
- `universe.symbols = ["QQQ"]`
- `computed_fields` defines moving averages.
- `signals.entry` and `signals.exit` compare short and long MA fields.
- `parameter_domains` includes values such as `short_ma` and `long_ma`.
- `fill_model` states next-bar/open/close behavior and cost assumptions.

Read after run:

- Metrics Overview for headline ranking.
- Parameter Matrix for candidate grid and robust shortlist.
- Backtests for selected candidate equity, drawdown, trade outcomes, and costs.

Do not treat the best Parameter Matrix row as OOS validation.

## QQQ Baseline + Quarterly TQQQ Short Overlay

Goal: normally hold `QQQ`; on the third Friday of March, June, September, and December, flatten QQQ at the open, short `TQQQ` at the open, cover `TQQQ` at the close, then restore QQQ on the next session open.

Expected config shape:

- Use a repo-supported calendar overlay mode. Do not use `dynamic_allocation_rules` as a runnable mode until the runtime, schema, tests, and support checker mark it active.
- `allocation.method = "calendar_event_overlay"` when available.
- Baseline weights include `QQQ: 1.0`.
- Event weights include `TQQQ: -1.0`.
- Calendar rule uses `months = [3, 6, 9, 12]`, `weekday = "friday"`, `ordinal = 3`.
- Execution uses exchange session open for event entry and session close for event exit.
- `risk.allow_short = true`.
- Costs and slippage are explicit.

Read after run:

- Backtests event/rebalance table, with exchange time visible.
- Expected sequence on an event date: QQQ exit at open, TQQQ new short at open, TQQQ close short at close, QQQ buy on next session open.
- Allocation timeline and target holdings, not only end-of-day holdings.

Do not call same-date open/close rows duplicated unless exchange time is missing.

## Multi-Asset Rotation

Goal: rotate between assets such as `VOO` and `GLD` using momentum rank and an eligibility filter.

Expected config shape:

- `platform.strategy_mode_id = "multi_asset_portfolio"`
- `selection.rank_by` names the score.
- Eligibility may use an SMA or price filter.
- `allocation` defines top-N, equal-weight, or position limit.
- `rebalance` defines daily/monthly/annual/event schedule.

Read after run:

- Metrics Overview for selected row.
- Backtests allocation changes, target holdings, active rebalances, turnover, asset contribution, and benchmark.
- Data lineage and universe provenance before performance claims.

Do not read portfolio allocation rows as the same thing as single-asset closed trades.

## Monthly Nth-Weekday Same-Session Event

Goal: buy an asset at the session open on the Nth weekday of each month and close it at the same session close, such as BTCUSDT month week 1-4 and weekday Monday-Sunday.

Expected config shape:

- `platform.strategy_mode_id = "calendar_event_session"`
- `platform.workflow_id = "parameter_matrix"` when sweeping month-week and weekday combinations.
- `signals.entry.op = "calendar.nth_weekday_of_month"`
- `signals.entry.ordinal` and `signals.entry.weekday` can use `param_ref`.
- `signals.exit.op = "session.same_session_close"`
- `fill_model.session_scope = "same_session"` with explicit entry and exit price assumptions.
- `parameter_domains` declares the tested calendar grid.

Read after run:

- Calendar signal audit and same-session execution records.
- Parameter Matrix for month-week / weekday candidate behavior.
- WFA only as research mechanics; calendar anomaly grids are sparse and can overfit easily.

Do not describe daily-bar same-session examples as executable intraday fills.

## Fixed Allocation / Rolling Validation

Goal: hold fixed weights and rebalance on schedule.

Expected config shape:

- `allocation.method` is fixed/static weights.
- No parameter domain means no Parameter Matrix optimization.
- Use rolling validation, not WFA optimization, when there are no tunable parameters.

Read after run:

- Allocation drift.
- Scheduled vs active rebalance counts.
- Turnover and cost drag.
- Rolling validation OOS windows if generated.

## WFA Optimization

Goal: test whether a parameterized strategy remains useful out of sample.

Expected config shape:

- A `wfa_run` config references a strategy config.
- IS/OOS windows are defined or auto-sized.
- Each window ranks candidates using IS data only.
- The official row is one selected optimum per window/objective.

Read after run:

- WFA verdict and selected optimum rows.
- Average OOS metrics, OOS positive window share, parameter stability, and OOS/IS ratio.
- Candidate diagnostics only as supporting evidence, never as pass/fail proof.
- Linked backtests when available.

## AI-Readable Review Pack

Use `outputs/app/ai_review/{run_id}/ai_review_pack.json` when asked to review results with AI.

Review order:

1. Run registry and stage status.
2. Artifact manifest and missing artifacts.
3. Embedded chart payloads.
4. Artifact table profiles.
5. `metric_field_catalog`.
6. Warnings and unavailable fields.

Absent fields mean `not generated` or `not available`, not zero.
