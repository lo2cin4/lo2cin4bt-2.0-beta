# Legacy Deprecation Cutover Plan (NodeIR-native Era)

## Scope
- Target domain: backtester + WFA strategy execution pipeline.
- Goal: retire legacy bridge/preset dependencies after one transition release.

## Remove In Cutover Release
1. WFA semantic legacy payload injection (`condition_pairs` / `indicator_params`) when `strategy_mode=semantic`.
2. Any runtime dependency on legacy-only preset naming (`MA1..MA12`, `HL*`, `BOLL*`) for new configs.
3. Legacy input discovery fallback paths for contracts under old `docs/contracts/*` truth sources.
4. Implicit `strategy_mode=auto` fallback to legacy when semantic contract paths are present.

## Removed Public Compatibility Layer
1. Public vector/sequential engine shims are removed.
2. `strategy_mode=auto` no longer falls back to public legacy execution.
3. New supported runs use `strategy_run` with `auto` / `node_ir`.

## Hard-Block After Transition Window
1. Reject new runs that set `strategy_mode=legacy` without override flag.
2. Reject configs with legacy-only fields when `strategy_mode=semantic`.
3. Remove bridge conversion code that mutates semantic into `condition_pairs`.

## Current Status (2026-04-12)
1. Item 1 is active:
   - `strategy_mode=legacy` requires explicit override flag.
2. Item 2 is active:
   - autorunner and WFA validators now reject `condition_pairs` / `indicator_params` when `strategy_mode=semantic`.
3. Item 3 is active:
   - standard semantic autorunner/WFA runtime no longer depends on hidden bridge injection
   - compiler no longer emits `legacy_adapter`
   - strategy contract no longer accepts top-level `legacy`

## Remaining Internal Cleanup Pointers
1. `wfanalyser/ParameterOptimizer_wfanalyser.py`
: internal adapter branches should continue moving toward semantic-native parameter plans.
2. `autorunner/BacktestRunner_autorunner.py`
: keep narrowing config normalization to `strategy_run` and portfolio paths.
3. `TradeSimulator_backtester.py`
: retained as a native helper used by NodeIR/native runtime simulation.

## Exit Criteria
1. `strategy_mode=semantic` + NodeIR path is default for autorunner and WFA.
2. Speed gate passes (`auto_vs_python <= 1.0`) on benchmark fixture.
3. No production run depends on bridge-generated `condition_pairs`.
4. Migration docs and examples all point to `workspace/strategies`, `workspace/features`, and `workspace/runs`.
