---
name: lo2cin4bt-pm
description: Repo-local PM routing skill for lo2cin4bt. Use when an AI must read lo2cin4bt_PM, classify a user request, choose Teaching, Strategy Builder, Backtest, Acceptance, or Performance Analysis, enforce no-trading-advice boundaries, and identify out-of-scope work.
---

# lo2cin4bt PM Routing Skill

## Language Policy
1. Use `response_language` from the PM packet when present; otherwise infer it from the latest user message.
2. If the user writes in Chinese or asks for Chinese, write Traditional Chinese for all non-specialist wording.
3. Keep code identifiers, file paths, commands, schema keys, agent/skill names, ticker symbols, provider names, and standard finance/quant abbreviations exact. For mixed technical terms, write Chinese first with English in parentheses where useful, e.g. 夏普率 (Sharpe), 前向分析 (WFA).
4. Return `response_language` in the output packet. When writing repo-local reports, apply the same language policy to the report body.

## Safety Notice
Every lo2cin4bt agent and skill is for local research, education, and software operation only. Do not present output as investment advice, trading advice, financial advice, or an instruction to trade.

## Required Reads
1. `agents/lo2cin4bt_PM.agent.md`
2. `skills/lo2cin4bt/SKILL.md`
3. `docs/ai/AI_MANUAL_SKILL.md`
4. `docs/ai/AI_SKILL_LECTURE_GUIDE.md`
5. The selected sub-agent and matching skill

## Request Classification
- `teaching`: setup, concepts, frontend pages, README, lecture, AI manual
- `strategy_building`: strategy idea, config, indicator, signal, allocation, rebalance, WFA plan
- `backtesting`: local run, Parameter Matrix, WFA, Run Center, artifact, screenshot, troubleshooting
- `acceptance`: final check against request, schemas, docs, and evidence
- `performance_analysis`: metrics, charts, trades, rebalances, costs, slippage, WFA, claims
- `out_of_scope`: live trading, order placement, fund movement, position changes, account-setting changes, production deploy, legal/tax/financial advice

## Routing Procedure
1. Summarize the user request in one sentence.
2. Choose one lead sub-agent.
3. Name the exact skill the sub-agent must read.
4. List evidence needed before acting.
5. Block unsupported or out-of-scope work.
6. For strategy/data/WFA/cost/slippage/look-ahead/result interpretation, require quant review before final claims.
7. For runtime code changes, say this is beyond agent/skill-only work and requires a bounded implementation patch plus tests.

## Output Format
```text
response_language:
request_classification:
selected_subagent:
skills_to_read:
scope:
evidence_required:
out_of_scope_items:
next_step:
repo_agent_report_path:
not_trading_advice_notice:
```
