---
name: lo2cin4bt
description: Operate, teach, and troubleshoot the lo2cin4bt quantitative research/backtesting repo. Use when Codex needs to install or launch lo2cin4bt, create or review strategy_run or wfa_run configs, run local backtests, Parameter Matrix, WFA or rolling validation, explain frontend metrics/artifacts/AI-readable packs, or recover beginner setup/runtime issues while respecting repo-only evidence and no-live-trading boundaries.
---

# lo2cin4bt

Use this skill as the repo-local operating guide for lo2cin4bt. Keep answers grounded in the repository, generated artifacts, and current app payloads. Label outside finance or engineering context as external context or AI inference.

## Safety Rules

- Do not deploy, place trades, enable live trading, move funds, change positions, or change external accounts. lo2cin4bt currently does not support order placement; broker/exchange accounts may be used only for read-only market-data access when the user asks for that setup.
- Do not invent config fields, strategy modes, metrics, provider behavior, WFA evidence, or UI features.
- Treat `strategy_run`, generated normalized snapshots, app payloads, and tests as the source of truth.
- If a result artifact is from an older contract and lacks fields required by current validation, call it stale and rerun with the current version instead of mixing old and new evidence.
- For public/GitHub guidance, assume runtime outputs, local configs, datasets, secrets, and broker credentials are not committed.

## Read Order

1. `AGENTS.md`
2. `README.md`
3. This `SKILL.md`
4. Load only the relevant reference below.
5. Inspect the actual config, payload, artifact, frontend component, or test before making a claim.

## Reference Map

- Public AI operator/teacher contract: `references/lo2cin4-agent-contract.md`
- README release acceptance criteria: `references/readme-acceptance-criteria.md`
- Beginner install and first successful run: `references/first-run.md`
- Supported feature recipes and first-run strategy examples: `references/feature-recipes.md`
- Strategy config sections and field choices: `references/strategy-config-fields.md`
- Frontend page walkthroughs and what each page can/cannot prove: `references/frontend-pages.md`
- Metric and field dictionary: `references/metric-dictionary.md`
- API, chart payload, artifact, and AI review pack map: `references/payload-contract-map.md`
- Quant interpretation risks and evidence boundaries: `references/quant-interpretation-risks.md`
- Current 2.0 troubleshooting: `references/troubleshooting.md`
- Contracts and schema index: `references/contracts-index.md`
- Workspace, GitHub upload, ignored output, and privacy boundary: `references/workspace-and-github-boundary.md`
- Done definition for this skill and teaching coverage: `references/acceptance-criteria.md`

## Core Workflows

### lo2cin4 Operator / Teacher

When a user says "you are lo2cin4" or asks the AI to develop a strategy, load `references/lo2cin4-agent-contract.md`, then use this skill plus the AI manual and lecture guide. In operator mode, start with a capability verdict before writing configs. In teacher mode, ground the lesson in repo files, tests, docs, configs, artifacts, or lecture pages.

### New User Setup

Read `references/first-run.md`. Walk the user from clone or ZIP download to `python main.py`, `http://127.0.0.1:2424/`, `scripts/doctor.py`, and one completed local run. First launch should auto-seed included examples into ignored `workspace/runs/` and `workspace/wfa/`. Only create/add configs when the user intentionally deleted them, wants a custom strategy, or the seed step failed and troubleshooting confirms the workspace is still empty. WFA configs should reference strategy configs with explicit `workspace/runs/<strategy-config>.json` paths.

### Strategy Creation

Read `references/strategy-config-fields.md` and `references/feature-recipes.md`. First produce a capability verdict:

```text
supported | needs_clarification | unsupported
```

Only write a config after provider, frequency, calendar/timezone, universe, benchmark, entry/exit or allocation rules, fill_model execution, cost/slippage, workflow, and parameter domains are known.

### Result Explanation

Read `references/frontend-pages.md`, `references/metric-dictionary.md`, and `references/quant-interpretation-risks.md`. Explain results in this order:

1. Config intent and workflow.
2. Data health, universe/provenance, benchmark, and truth warnings.
3. Strategy performance and risk metrics.
4. Portfolio holdings, rebalance, contribution, costs, and risk gates.
5. Parameter Matrix or WFA evidence only when those artifacts exist.
6. Missing or unavailable fields as `not generated`, never as zero.

### Troubleshooting

Read `references/troubleshooting.md`. Check setup, `scripts/doctor.py`, frontend build, port `2424`, Run Center config discovery, app registry, current payload JSON, and then source artifacts. Do not judge from screenshots alone.

## Closeout Checklist

- Cite files, configs, payload paths, tests, or artifacts used as evidence.
- State whether the action is local research only.
- State any stale artifact, missing output, benchmark mismatch, or survivorship/provenance risk.
- When teaching, give the next exact UI page or file the user should inspect.
