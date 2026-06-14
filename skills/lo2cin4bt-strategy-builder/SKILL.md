---
name: lo2cin4bt-strategy-builder
description: Repo-local strategy builder skill for lo2cin4bt. Use when converting plain-language strategy ideas into supported, needs-clarification, or unsupported verdicts, and when drafting strategy_run or wfa_run configs from Strategy Building Blocks.
---

# lo2cin4bt Strategy Builder Skill

## Language Policy
1. Use `response_language` from the PM packet when present; otherwise infer it from the latest user message.
2. If the user writes in Chinese or asks for Chinese, write Traditional Chinese for all non-specialist wording.
3. Keep code identifiers, file paths, commands, schema keys, agent/skill names, ticker symbols, provider names, and standard finance/quant abbreviations exact. For mixed technical terms, write Chinese first with English in parentheses where useful, e.g. 夏普率 (Sharpe), 前向分析 (WFA).
4. Return `response_language` in the output packet. When writing repo-local reports, apply the same language policy to the report body.

## Safety Notice
Strategy-building output is local research support only. It is not investment advice, trading advice, financial advice, or an instruction to trade.

## Required Reads
- `references/strategy-authoring-template.md`
- `references/strategy-config-fields.md`
- `references/indicator-recipes.md`
- `docs/STRATEGY_BUILDING_BLOCKS.md`
- `backtester/contracts/strategy_authoring/strategy-authoring-layers-v1.json`
- Relevant schemas under `backtester/contracts/strategy/`

## Capability Verdict
Return one verdict before writing any runnable config:

- `supported`
- `needs_clarification`
- `unsupported_needs_new_building_block`

## Parse The Strategy Into
- asset or universe
- data provider
- frequency
- calendar and timezone
- strategy mode and workflow
- computed fields
- signals
- selection
- allocation
- rebalance
- fill timing
- costs and slippage
- benchmark
- risk gates
- parameter domains
- outputs

## Parameter Intent Detection
Before writing a config, decide whether the user supplied fixed values or tunable domains.

- Explicit multiple values, ranges, step sizes, or sweep words mean Parameter Matrix. Examples: `threshold 10/11/12/13/14/15`, `holding_days 50,100,150`, `MA 20 to 100 step 10`, `test different thresholds`, `sweep`, `matrix`, `optimize`, or `try 10-15`.
- Single literal values mean Single Backtest unless the user explicitly asks to compare alternatives. Examples: `threshold 15` or `hold 250 days`.
- If a strategy has obvious tunable variables but the user did not give candidate values, return `needs_clarification` and ask whether those variables should be fixed or tested as a matrix. Common tunables include threshold, lookback, moving-average period, holding days, rebalance interval, top-N count, allocation weights, stop/take-profit size, ranking window, and volatility target.
- If the user confirms matrix testing after clarification, write one `parameter_matrix` config with all axes in `parameter_domains`.

## Rules
1. Define calculations in `computed_fields[]` before using them.
2. Use canonical indicator names such as `indicator.sma`, `indicator.rsi`, `indicator.macd`, `indicator.atr`, `indicator.zscore`, `indicator.percentile`, and `indicator.bollinger`.
3. Do not use inline feature nodes.
4. Treat condition logic, comparators, cross conditions, calendars, fill timing, and strategy templates as separate Strategy Building Block types.
5. For any named pattern, custom signal, or undefined setup, ask for observable OHLCV conditions first.
6. For same-session fills, prove the signal is known before the fill or reject it.
7. Do not write a runnable config for unsupported behavior.
8. Do not treat a category listed in `docs/STRATEGY_BUILDING_BLOCKS.md` as supported unless its `support_status` is `registry_supported` and its maturity is public-supported.

## Default Strategy Development Workflow
1. Run the workspace intake gate first when the idea needs any user-provided local file, external event data, custom universe membership, custom benchmark data, custom factor data, or indicator extension.
2. Check existing Strategy Building Blocks and registry ops before proposing new code.
3. If existing blocks cover the idea and intake has passed, write a config-only `strategy_run` draft.
4. Put all derived values in `computed_fields[]`, then reference those names from `signals`, `selection`, or `allocation`.
5. Use `fill_model` for timing, price basis, cost, slippage, accounting, and session assumptions.
6. Use `wfa_run` only as a wrapper that references a strategy config; do not duplicate strategy logic inside WFA.
7. Run `python scripts/workspace_doctor.py --config <path>` before calling any config runnable.
8. Route strategy, data, WFA, cost/slippage, look-ahead, or backtest validity changes to Quant review.
9. If a needed indicator is not built in, tell the user a custom indicator is required, keep it under `workspace/indicators/extensions/<slug>/`, and require `indicator_doctor` plus runtime dispatch support before producing a runnable config.

## Workspace Intake Gate
For user-provided data that affects signals, selection, allocation, universe membership, benchmark, or WFA, a formal description is required.

Known provider OHLCV can be described directly inside `strategy_run` `data` and `universe` when no local file is being joined. Local CSV/Excel/Parquet files require a feature contract, supported frame reference checked by `workspace_doctor`, or a new contract/building block.

Every feature contract must include `data_availability` for each feature:

- `observed_at`: when the value is first observable.
- `usable_from`: earliest simulated trading time that may use the value.
- `point_in_time`: whether the source preserves the value as known at that time.
- `revision_policy`: `point_in_time`, `declared_static`, or `revised_history`.

If any data-availability field is unknown, return `needs_clarification` and ask for the data publication / revision timing. Do not assume point-in-time status, do not claim same-bar usability without proof, and do not write a runnable config until `workspace_doctor` accepts the contract.

If required files or timing assumptions are missing, return `needs_clarification` with the exact file, target `workspace/` path, required columns/schema, timing questions, and conversion policy.

## Missing OP / Building Block Flow
When an idea needs a missing OP, unsupported pattern/setup, order model, or calendar rule:

1. Return `needs_clarification` if the observable definition, data timing, entry, exit, invalidation, or fill timing is unclear.
2. Return `unsupported_needs_new_building_block` when no supported OP exists.
3. Do not write a runnable config until the missing block is implemented and reviewed.
4. Required implementation deliverables are:
   - registry entry and public op name
   - runtime implementation
   - schema or validator coverage
   - oracle tests for no-look-ahead behavior
   - golden/parity tests for expected signals or trades
   - docs/examples that label the feature accurately
   - quant safety metadata covering observation time, data availability time, earliest trade time, warmup/lookback, cost/slippage, WFA train/OOS behavior, and missing-data policy
   - promotion packet or block spec matching `strategy_building_block.v1`
5. For Pine Script translations, separate pattern detection, setup state, order model, exits, and visualization-only labels before deciding what lo2cin4bt must implement.


## Parameter Matrix Shape Rules
- If the user supplied parameter ranges or multiple candidate values in the original prompt, do not ask for extra confirmation before selecting `workflow_id = "parameter_matrix"`; auto-detect the sweep and state the inferred parameter domains in the output packet.
- If the user supplied no ranges for obvious tunables, ask a short clarification before drafting: "Use fixed values, or test multiple values as a Parameter Matrix?"
- If the user confirms a parameter sweep or "all matrix params", write one `workflow_id = "parameter_matrix"` config with all tunable axes in `parameter_domains`. Do not split each parameter combination into separate `single_backtest` configs.
- `workflow_id = "parameter_matrix"` must expand to at least two candidate combinations. A one-combo matrix is a mislabelled single policy and must be corrected before running.
- `workflow_id = "single_backtest"` must not carry non-empty `parameter_domains`; use resolved literal values in the strategy body instead.


## Repo-Local Report Output
When a durable repo-local report is requested, write Markdown under `workspace/reports/agents/<agent_name>/YYYY-MM-DD_<short-topic>.md` and return `repo_agent_report_path` in the output packet.

## Output Format
```text
response_language:
capability_verdict:
parsed_strategy_intent:
building_blocks_checked:
mapped_categories:
required_blocks:
missing_or_ambiguous_items:
workspace_intake_status:
config_status:
maturity_gate:
support_status:
evidence_paths:
config_paths:
validation_command:
quant_review_required:
repo_agent_report_path:
not_trading_advice_notice:
```
