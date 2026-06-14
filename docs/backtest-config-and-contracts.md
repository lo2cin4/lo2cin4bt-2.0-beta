# Backtest Config and Contracts

## User Entry Points
Current user-editable inputs and curated release examples live under `workspace/`:
- `workspace/datasets/`
- `workspace/features/`
- `workspace/indicators/`
- `workspace/strategies/`
- `workspace/runs/`
- `workspace/wfa/`

Sharing workspace files is supported through the existing folder design: the receiver must place every referenced file at the same relative path and run the workspace checks. `workspace/indicators/extensions/` contains executable custom code, so a package is usable only after manifest validation, `indicator_doctor`, and matching runtime dispatch support. If runtime support is absent, an AI agent must report the missing building block instead of producing a runnable config.

Current app-managed outputs are generated under `outputs/app/`:
- `outputs/app/run_registry/`
- `outputs/app/run_snapshots/`
- `outputs/app/artifact_manifests/`
- `outputs/app/chart_payloads/`
- `outputs/app/ai_review/`

`outputs/` is runtime state and is intentionally ignored by Git. Recreate it by
running app jobs from Run Center or by executing the relevant verification
scripts. Old module-specific output roots are legacy implementation details, not
the public app contract. `records/` is no longer part of the active input/output
path.

## Canonical Contract Roots
Backtest contract truth lives under `backtester/contracts/`:
- strategy schema: `backtester/contracts/strategy/strategy-contract.schema.json`
- feature schema: `backtester/contracts/feature/feature-contract-v1.schema.json`
- indicator manifest schema: `backtester/contracts/indicator-manifest/indicator-manifest-v1.schema.json`

## Current Runtime Truth
For new runs, the supported path is:
- run config in `workspace/runs/*.json`
- `backtester.strategy_mode = "semantic"`
- `strategy_contract_path` points to a strategy contract
- optional `feature_contract_path` points to a feature contract v1
- runtime compiles into `execution_plan`
- autorunner and WFA execute the semantic/native path

The following legacy fields are no longer allowed in `strategy_mode = "semantic"` run configs:
- `backtester.condition_pairs`
- `backtester.indicator_params`

Validators now fail fast if those fields appear in a semantic run config.

## Strategy Contract
Strategy contract defines:
- semantic `entry` / `exit` node trees
- `parameter_domains` for sweep and optimization
- `engine_preferences` for `auto|node_ir`
- `data_context.feature_contract_ref` for feature alignment
- `max_combos` / `combo_limits` for parameter-space control

It no longer supports a top-level `legacy` payload.

Example semantic condition:
- `feature.vix.close < 30 AND price.close > ta.sma(price.close, 200)`

## Feature Contract v1
Feature contract v1 is already multi-feature by design.

Each item in `features[]` declares:
- a semantic field name, for example `feature.vix.close`
- a source type and URI
- a source column
- frequency / timezone / fill policy / lag
- optional `source.source_id` for multi-source auditability
- optional `calendar` / `staleness_max_bars` for contract-safe alignment metadata

This means current semantic strategy contracts can already express:
- `A condition only`
- `A AND B`
- `A AND B AND C`

As long as the referenced fields are declared in the feature contract.

## Current Multi-Feature Capability
Today, the semantic runtime already supports multi-feature conditions such as:
- `feature.vix.close < 30`
- `feature.macro.regime == 1`
- `price.close > ta.sma(price.close, 200)`

Combined in one strategy:
- `A AND B AND C`

This is the recommended path for future multi-factor logic.

Runtime materialization support:
- node_ir/native execution can materialize local feature-contract sources into the runtime dataframe
- current supported runtime join modes are `left` and `asof`
- current supported source file types are CSV / Excel / parquet
- `fill_policy`, `lag_bars`, and `asof_tolerance_bars` are applied during runtime materialization
- if a declared source file is absent but the needed source column already exists in the loaded base dataframe, runtime can fallback to the base dataframe column as a compatibility bridge

## Current Limitation
Some downstream compatibility fields still use the old single-label wording:
- `selected_predictor`
- `Predictor_value`

Those fields are kept only to preserve existing consumer/output contracts.
They should be understood as compatibility labels, not as a limit on semantic strategy inputs.

## Forward Design: Multi-Predictor / Multi-Source
Future versions should formalize a broader feature-input model:
- multiple external features from one dataset
- multiple external features from multiple datasets
- mixed conditions such as `A condition + B condition + price condition`

Planned design direction:
1. keep strategy expressions field-based, not predictor-slot-based
2. keep feature contract as the single declaration layer for external inputs
3. treat `selected_predictor` as a legacy consumer label to be retired later
4. introduce a clearer term such as `input_features` or `feature_bindings` in a later cutover

## Indicator Manifest
Indicator manifest remains the registry for indicator metadata and implementation mapping.

It defines:
- indicator family identity
- parameter metadata
- implementation binding
- compatibility aliases during the transition window
- extension package discovery under `workspace/indicators/extensions/*/manifest.json`
- optional `input_contract` metadata for multi-column indicators

Trading actions do not belong in manifest metadata. They belong in strategy semantics.

Current extension direction:
- core indicators remain in `backtester/contracts/indicator-manifest/manifests/core/`
- user indicators live in workspace packages and can bind Python implementations through `artifact_path + entrypoint`
- multi-column custom indicators are supported through indicator params such as `primary_column` / `confirm_column`
- prefer custom indicators that emit conditions or confirmations; keep exit semantics in the strategy contract when possible
- passing the indicator manifest / doctor checks does not by itself prove a strategy can run; runtime dispatch and a smoke backtest are still required

## Current Data Breadth Direction
Feature contract v1 now has a stronger multi-source direction:
- `source.source_id` can identify the originating dataset/source explicitly
- `source.dayfirst` and `source.time_format` can pin non-ISO file timestamp parsing
- `alignment_policy.calendar_policy` can document how calendars should be handled
- `alignment_policy.asof_tolerance_bars` can document asof tolerance
- `feature.calendar` and `feature.staleness_max_bars` can make cross-source assumptions auditable

Current runtime boundary:
- multi-dataset execution is now available for local file-based feature sources in node_ir/native backtests and WFA
- runtime still treats the loaded base dataframe as the tradable anchor
- semantic CSV / Excel / parquet exports now share the same semantic filename pattern
- `left` joins now reject duplicate source keys instead of silently multiplying rows
- `asof` joins now respect instrument matching even when base/source instrument field names differ
- broader calendar normalization, multi-instrument joins, and richer source backends remain future work

Recommended example:
- `backtester/contracts/feature/examples/feature-contract-multisource-v1.json`
- `workspace/features/feature-contract-multisource-v1.user.json`

## Migration Notes
- `docs/contracts/*` is no longer a truth source for active backtest contracts
- `workspace/` is the only supported user input root
- `outputs/` is the only supported runtime output root
- legacy config semantics are now transition-only and blocked in semantic run configs
