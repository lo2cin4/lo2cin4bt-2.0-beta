---
name: lo2cin4bt-acceptance
description: Repo-local acceptance skill for lo2cin4bt. Use when checking whether a deliverable satisfies the user request, repo schemas, skills, docs, tests, stale paths, forbidden actions, and no-trading-advice disclaimers.
---

# lo2cin4bt Acceptance Skill

## Language Policy
1. Use `response_language` from the PM packet when present; otherwise infer it from the latest user message.
2. If the user writes in Chinese or asks for Chinese, write Traditional Chinese for all non-specialist wording.
3. Keep code identifiers, file paths, commands, schema keys, agent/skill names, ticker symbols, provider names, and standard finance/quant abbreviations exact. For mixed technical terms, write Chinese first with English in parentheses where useful, e.g. 夏普率 (Sharpe), 前向分析 (WFA).
4. Return `response_language` in the output packet. When writing repo-local reports, apply the same language policy to the report body.

## Safety Notice
Acceptance review checks deliverable quality only. It does not endorse any strategy and is not investment advice, trading advice, financial advice, or an instruction to trade.

## Required Reads
- The user request and latest corrections
- The agent and skill instructions touched by the task
- Relevant README/doc/config/test artifacts
- `references/readme-acceptance-criteria.md` when README or user-facing docs are touched
- `references/lo2cin4bt-agent-contract.md` when AI agent discoverability is touched
- `references/workspace-and-github-boundary.md` when workspace sharing or upload behavior is touched

## Checklist
- user wording followed
- latest correction supersedes older instructions
- no scope drift
- required agents/skills/references exist
- no unsupported strategy/config claim
- no public performance overclaim
- no live trading, order placement, fund movement, position changes, account-setting changes, or production deployment
- docs and skills agree on paths and names
- evidence is current
- tests or scans match change risk

## Verdicts
- `pass`
- `revise`
- `block`

## Output Format
```text
response_language:
acceptance_verdict:
requirements_checked:
evidence_checked:
gaps:
scope_drift:
forbidden_or_out_of_scope_items:
required_followup_gate:
repo_agent_report_path:
not_trading_advice_notice:
```
