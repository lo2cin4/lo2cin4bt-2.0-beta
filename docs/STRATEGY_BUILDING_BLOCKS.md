# Strategy Building Blocks

Strategy Building Blocks are the tested pieces lo2cin4bt can use when turning a plain-language strategy idea into a runnable config.

This document is the public support map. If a strategy asks for behavior outside these supported blocks, AI must return `unsupported_needs_new_building_block` or ask for clarification. It must not create hidden synthetic data or pretend the engine supports behavior that is not implemented.

## Supported Registry Blocks

These block kinds are part of the current public authoring surface:

| Block kind | Typical config use | Public support status |
| --- | --- | --- |
| `indicator` | `computed_fields` values such as moving averages, RSI, ATR, z-score, percentile, Bollinger-style bands | Supported when listed in the op registry and accepted by schema/checkers |
| `condition_logic` | combine conditions with all/any/not logic | Supported |
| `condition_comparator` | compare fields, constants, or computed values | Supported |
| `cross_condition` | detect current/previous bar crossings | Supported |
| `calendar` | scheduled or session-based rules | Supported for documented calendar/session ops |
| `execution` | fill timing, price basis, delay bars, costs, slippage, accounting assumptions | Supported only through documented `fill_model` fields |
| `strategy_template` | reusable authoring scaffold that expands into a config | Supported only for documented templates |

Other strategy areas, such as data provider, allocation method, cost model, slippage model, risk, benchmark, and validation workflow, are still normal `strategy_run` config fields unless promoted into the registry with implementation and tests.

## Support Status Terms

Use these exact statuses in agent output:

- `registry_supported`: implemented, registered, schema-checked, and covered by tests.
- `config_field_only`: supported as a structured config field, not as a standalone registry op.
- `planned`: documented as a future direction, not runnable today.
- `unsupported`: not available in the current engine.

Only `registry_supported` blocks and documented `config_field_only` behavior can justify a `supported` verdict.

## Promotion Requirements

A new block becomes public-supported only after all of these exist:

- public block name and registry entry
- runtime implementation
- schema or validator coverage
- support checker entry
- oracle or golden tests
- end-to-end backtest coverage when the block can affect trades
- frontend/payload display coverage when the block appears in results
- documentation or example that describes the limitation clearly
- quant safety metadata for observation time, data availability, earliest trade time, warmup/lookback, fill policy, missing-data policy, cost/slippage, and WFA train/out-of-sample behavior

Until then, the correct answer is `unsupported_needs_new_building_block`.

## Strategy Builder Rules

Before writing a runnable config, the Strategy Builder must:

1. Parse the user's idea into assets, data source, frequency, calendar, indicators, entry, exit, allocation, fill timing, cost/slippage, benchmark, and validation workflow.
2. Check every required block against the public registry and schema.
3. Ask for clarification if a required data file, publication time, parameter range, fill timing, or benchmark is unclear.
4. Refuse runnable configs for unsupported named patterns, custom signals, or data joins until the missing block is implemented and tested.
5. Keep all generated strategy artifacts inside `workspace/` unless the user explicitly asks for engine development.

## Look-Ahead Boundary

No block may use future information:

- A signal for one bar must not depend on prices, labels, ranks, or revisions from later bars.
- Data known only after the close cannot trigger a same-bar open trade.
- Local files that affect trades need a data availability description.
- `revision_policy: revised_history` is research/demo data or requires review; it is not point-in-time proof.
- WFA parameters must be selected on the training window and evaluated on the out-of-sample window.

When in doubt, ask the user or return `needs_clarification`.
