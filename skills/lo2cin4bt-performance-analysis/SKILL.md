---
name: lo2cin4bt-performance-analysis
description: Repo-local performance analysis skill for lo2cin4bt. Use when explaining generated metrics, equity curves, drawdowns, trades, rebalances, costs, slippage, benchmarks, Parameter Matrix, WFA, missing fields, and claims that require QuantReview.
---

# lo2cin4bt Performance Analysis Skill

## Language Policy
1. Use `response_language` from the PM packet when present; otherwise infer it from the latest user message.
2. If the user writes in Chinese or asks for Chinese, write Traditional Chinese for all non-specialist wording.
3. Keep code identifiers, file paths, commands, schema keys, agent/skill names, ticker symbols, provider names, and standard finance/quant abbreviations exact. For mixed technical terms, write Chinese first with English in parentheses where useful, e.g. 夏普率 (Sharpe), 前向分析 (WFA).
4. Return `response_language` in the output packet. When writing repo-local reports, apply the same language policy to the report body.

## Safety Notice
Performance analysis explains generated artifacts only. It is not investment advice, trading advice, financial advice, or an instruction to trade.

## Required Reads
- `references/frontend-pages.md`
- `references/metric-dictionary.md`
- `references/payload-contract-map.md`
- `references/quant-interpretation-risks.md`
- The exact config, payload, result folder, or frontend page assigned by the PM agent

## Audience Rule
Default to a beginner-readable report. Put audit details in a technical appendix unless the user explicitly asks for raw evidence.

Beginner users usually want:

1. one-sentence summary
2. what the strategy did
3. which run is being analyzed
4. data and benchmark caveats
5. main results and benchmark comparison
6. risk and drawdown
7. trade, holding, rebalance, and portfolio evidence
8. cost and slippage evidence
9. frontend data completeness
10. what can and cannot be concluded
11. next research steps

Do not lead with raw field names such as `artifact_scope` or `configs_and_payloads_read` in user-facing reports.

## Writing Style
- Write in a professional, concise, artifact-backed style.
- Do not use rhetorical contrast templates that replace one idea with a dramatic opposing idea.
- Do not add motivational, moralizing, or AI-like commentary.
- Prefer direct caveats, short tables, and traceable numbers.

## Frontend Completeness Gate
Before producing a complete analysis, inspect every available frontend-facing payload and AI-readable pack for the run.

Required roots:

- `outputs/app/run_registry/{run_id}.json`
- `outputs/app/chart_payloads/{run_id}/`
- `outputs/app/ai_review/{run_id}/ai_review_pack.json`
- `outputs/app/artifact_manifests/{run_id}.json`
- `outputs/app/run_snapshots/{run_id}/`

Before marking Backtests Detail missing, check `backtest_result_index.json`. If it has a `backtest_id`, request or generate the detail payload through the local app API/service and then analyze the generated `backtest_detail_*.json`.

Frontend payloads to check when present:

- `metrics_overview_payload.json`
- `parameter_heatmap_payload.json`
- `parameter_matrix_payload.json`
- `backtest_detail_*.json`
- `wfa_dashboard_payload.json`
- `statanalyser_summary_payload.json`

AI review pack sections to check when present:

- `source_payloads`
- `snapshot_payloads`
- `payload_index`
- `artifact_table_profiles`
- `metric_field_catalog`

Snapshot and artifact evidence to check when present:

- `strategy_run.json`
- `run_config.json`
- `backtest_result_index.json`
- `data_lineage_manifest.json`
- `managed_artifacts/portfolio/*equity*`
- `managed_artifacts/portfolio/*holdings*`
- `managed_artifacts/portfolio/*rebalance*`
- WFA parquet/metadata artifacts

If a frontend page cannot be fully analyzed because its payload is missing, say so plainly:

- `Metrics Overview`: required for any performance summary
- `Backtests Detail`: required for complete single-run drilldown; generate/read it on demand when `backtest_result_index.json` has a `backtest_id`
- `Parameter Matrix`: required only when the run has parameter domains or matrix workflow
- `WFA`: required only for WFA/rolling runs or when discussing WFA, robustness, rolling validation, or OOS behavior; for `single_backtest`, mark it `not applicable`
- `Factor Analysis`: not applicable unless statanalyser payload exists

Do not call a requested report complete when required payloads are missing. Use `partial analysis` and list exactly what must be generated, regenerated, or run through a separate workflow.

## Procedure
1. Identify the user-facing run facts: run id, status, created/completed time, display label, strategy mode, workflow, and config filename.
2. Inspect all frontend payloads listed in the Frontend Completeness Gate, including lazy-generated Backtests Detail when available.
3. State data and benchmark caveats before returns.
4. Explain only generated metrics and fields.
5. Mark missing fields as `not generated` or `not applicable`.
6. For Parameter Matrix, describe candidate ranking, not future performance.
7. For WFA, distinguish in-sample selection from out-of-sample evaluation.
8. List what can be concluded, what cannot be concluded, and what needs QuantReview.

## Analysis Categories
Performance analysis can cover these artifact-backed categories:

1. Run analyzed: run id, config filename, workflow, timestamp, display label, and whether outputs are current.
2. Data and benchmark caveats: provider, symbol mapping, frequency, calendar, timezone, effective start, missing assets, benchmark label, and lineage status.
3. Strategy/result summary: strategy label, generated metrics, equity curve, drawdown, benchmark comparison, and data health warnings.
4. Trade and portfolio evidence: trade rows, holdings, allocation changes, target weights, rebalance audit, asset contribution, turnover, and risk gate events.
5. Cost and slippage evidence: configured transaction costs, slippage assumptions, generated cost drag, gross/net availability, and missing cost fields.
6. Frontend completeness: which app pages have generated payloads and which are missing.
7. Parameter Matrix screening: parameter axes, objectives, ranking config, shortlist rows, cluster/plateau diagnostics, and parameter importance.
8. WFA or rolling validation: train/test window boundaries, selected optimum rows, diagnostic rows, OOS metrics, OOS/IS ratios, portfolio window summary, and truth warnings.
9. Claim gate: statements that are allowed, statements that are blocked, and whether final wording requires QuantReview.

## What The Analysis Is Not
- It is not a buy/sell recommendation.
- It is not proof that a strategy is profitable or suitable for live trading.
- It is not a replacement for payloads or artifacts; screenshots alone are not enough.
- It does not turn missing fields into zero.
- It does not treat Parameter Matrix ranking as OOS proof.
- It does not treat WFA as a guarantee of future performance.


## Repo-Local Report Output
When a durable repo-local report is requested, write Markdown under `workspace/reports/agents/<agent_name>/YYYY-MM-DD_<short-topic>.md` and return `repo_agent_report_path` in the output packet.

## QuantReview Triggers
Require quant review before final wording when public docs or user conclusions mention strategy performance, WFA, robustness, overfitting, alpha, tradability, data lineage, cost/slippage impact, or look-ahead risk.

## Default User-Facing Output Format
```text
response_language:
title:
repo_agent_report_path:
not_trading_advice_notice:
one_sentence_summary:
strategy_plain_english:
run_analyzed:
data_and_benchmark_caveats:
main_results:
benchmark_comparison:
risk_and_drawdown:
trade_holding_rebalance_evidence:
cost_slippage_caveats:
frontend_data_completeness:
what_this_can_mean:
what_this_cannot_mean:
next_research_steps:
quant_review_required:
technical_appendix:
```

## Technical Appendix Fields
Use these in the appendix, not as the first section of a beginner report:

```text
artifact_scope:
configs_and_payloads_read:
payloads_read:
snapshots_read:
artifact_profiles_read:
missing_fields:
claims_allowed:
claims_blocked:
```
