# Feature Recipes

Use these as beginner-safe patterns. Always inspect the actual config before running.

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
- `fill_model` states open/close prices, delay bars, cost, and slippage assumptions.

Read after run:

- Metrics Overview for headline ranking.
- Parameter Matrix for candidate grid and robust shortlist.
- Backtests for selected candidate equity, drawdown, trade outcomes, and costs.

Do not treat the best Parameter Matrix row as OOS validation.

## QQQ Baseline + Quarterly TQQQ Short Overlay

Goal: normally hold `QQQ`; on the third Friday of March, June, September, and December, flatten QQQ at the open, short `TQQQ` at the open, cover `TQQQ` at the close, then restore QQQ on the next session open.

Expected config shape:

- Use only a repo-supported calendar/session or portfolio mode.
- Do not emit `dynamic_allocation_rules` as a runnable mode until schema, support checker, runtime, oracle tests, docs, and QuantReview confirm support.
- If the requested overlay is unsupported, return `unsupported_needs_new_building_block`.
- Baseline weights include `QQQ: 1.0`.
- Event weights include `TQQQ: -1.0`.
- Calendar rule uses `months = [3, 6, 9, 12]`, `weekday = "friday"`, `ordinal = 3`.
- `fill_model` must make exchange session open/close and delay-bar behavior explicit.
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
