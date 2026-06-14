# Quant Validation Gates

These gates protect backtest mechanics.

## Covered Risks

- Cost and slippage are tested with regression tests.
- Open/close execution timing and `fill_model` delay bars are covered by oracle tests.
- Parameter matrix shape is checked by config and workflow tests.
- WFA selection is tested so out-of-sample results do not silently use future-selected parameters.
- Local datasets and universe files must be explicit; AI should not invent hidden synthetic series.
- Feature contracts must declare data availability. `revision_policy: revised_history` is allowed only with a warning and review note; it is not point-in-time evidence.

## Required Review

Any change touching strategy logic, data sources, WFA, cost, slippage, benchmark, survivorship, universe provenance, or look-ahead assumptions needs QuantReview.

## Boundary

These tests prove mechanics, not trading edge. A green backtest is not investment advice.

`workspace_doctor` can reject obvious timing mistakes and warn on revised-history data, but it cannot prove that a vendor feed is survivorship-free or point-in-time complete. Public docs, lecture material, and AI summaries must describe revised-history examples as research demonstrations unless a true point-in-time source is supplied.
