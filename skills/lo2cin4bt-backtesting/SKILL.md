---
name: lo2cin4bt-backtesting
description: Repo-local backtesting skill for lo2cin4bt. Use when running or troubleshooting local strategy_run, Parameter Matrix, WFA, rolling validation, Run Center discovery, frontend startup, payload refresh, screenshots, or generated artifacts.
---

# lo2cin4bt Backtesting Skill

## Language Policy
1. Use `response_language` from the PM packet when present; otherwise infer it from the latest user message.
2. If the user writes in Chinese or asks for Chinese, write Traditional Chinese for all non-specialist wording.
3. Keep code identifiers, file paths, commands, schema keys, agent/skill names, ticker symbols, provider names, and standard finance/quant abbreviations exact. For mixed technical terms, write Chinese first with English in parentheses where useful, e.g. 夏普率 (Sharpe), 前向分析 (WFA).
4. Return `response_language` in the output packet. When writing repo-local reports, apply the same language policy to the report body.

## Safety Notice
Backtests are local research artifacts only. They are not investment advice, trading advice, financial advice, or instructions to trade.

## Required Reads
- `references/first-run.md`
- `references/troubleshooting.md`
- `references/payload-contract-map.md`
- `references/frontend-pages.md`
- The exact config assigned by the PM agent

## Pre-Run Checklist
1. Confirm config path exists.
2. Confirm schema version and workflow.
3. Run `python scripts/workspace_doctor.py --config <config>` and stop on any `[FAIL]`.
4. Confirm provider, symbol, frequency, calendar, and benchmark.
5. Confirm costs and slippage are explicit or intentionally defaulted.
6. Confirm output path is local.
7. Confirm no live trading, order placement, fund movement, position change, or account-setting-change path is involved.

## Procedure
1. Run the smallest relevant validation first.
2. Use repo commands from docs/tests; do not invent commands.
3. Record command, config path, stdout/stderr summary, artifact paths, and payload paths.
4. If frontend is involved, verify expected payloads are visible.
5. If artifacts are stale, rerun with current config instead of mixing old and new outputs.


## Repo-Local Report Output
When a durable repo-local report is requested, write Markdown under `workspace/reports/agents/<agent_name>/YYYY-MM-DD_<short-topic>.md` and return `repo_agent_report_path` in the output packet.

## Output Format
```text
response_language:
run_goal:
configs_used:
workspace_doctor_command:
commands_run:
artifacts_created_or_read:
payloads_checked:
failure_recovery:
remaining_blockers:
quant_review_required:
repo_agent_report_path:
not_trading_advice_notice:
```
