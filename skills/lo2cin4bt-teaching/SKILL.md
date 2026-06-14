---
name: lo2cin4bt-teaching
description: Repo-local teaching skill for lo2cin4bt. Use when explaining setup, AI usage, repo structure, frontend pages, metrics, README, Lecture Skill, AI Manual Skill, terminology, or beginner next steps from repo evidence only.
---

# lo2cin4bt Teaching Skill

## Language Policy
1. Use `response_language` from the PM packet when present; otherwise infer it from the latest user message.
2. If the user writes in Chinese or asks for Chinese, write Traditional Chinese for all non-specialist wording.
3. Keep code identifiers, file paths, commands, schema keys, agent/skill names, ticker symbols, provider names, and standard finance/quant abbreviations exact. For mixed technical terms, write Chinese first with English in parentheses where useful, e.g. 夏普率 (Sharpe), 前向分析 (WFA).
4. Return `response_language` in the output packet. When writing repo-local reports, apply the same language policy to the report body.

## Safety Notice
Teaching is educational only. It is not investment advice, trading advice, financial advice, or an instruction to trade.

## Required Reads
Load only what the lesson needs:

- `README.md`
- `docs/ai/AI_MANUAL_SKILL.md`
- `docs/ai/AI_SKILL_LECTURE_GUIDE.md`
- `references/first-run.md`
- `references/frontend-pages.md`
- `references/metric-dictionary.md`
- `references/troubleshooting.md`

## Procedure
1. Identify the learner goal.
2. Name the repo evidence used.
3. Explain implemented lo2cin4bt behavior separately from general trading theory.
4. Define technical terms at first use.
5. Mention what a page or artifact can prove and cannot prove.
6. End with one concrete next file, page, command, or artifact.


## Repo-Local Report Output
When a durable repo-local report is requested, write Markdown under `workspace/reports/agents/<agent_name>/YYYY-MM-DD_<short-topic>.md` and return `repo_agent_report_path` in the output packet.

## Stop Conditions
Stop when the request needs trading advice, unsupported repo behavior, unavailable private credentials, paid data access, or old artifacts that conflict with current contracts.

## Output Format
```text
response_language:
lesson_goal:
repo_evidence:
plain_explanation:
terms:
not_generated_or_not_applicable:
next_step:
repo_agent_report_path:
not_trading_advice_notice:
```
