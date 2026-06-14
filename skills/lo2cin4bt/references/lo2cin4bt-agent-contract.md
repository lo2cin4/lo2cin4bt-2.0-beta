# lo2cin4bt_PM Agent Contract

New strategy, backtest, acceptance, and performance-analysis work should start from `agents/lo2cin4bt_PM.agent.md`. Teaching work may start directly from `agents/lo2cin4bt_Teacher.agent.md`.

## Agent Suite

- Main coordinator: `agents/lo2cin4bt_PM.agent.md`
- Teacher: `agents/lo2cin4bt_Teacher.agent.md`
- Strategy building: `agents/lo2cin4bt_StrategyBuilderSubAgent.agent.md`
- Backtesting: `agents/lo2cin4bt_BacktestSubAgent.agent.md`
- Acceptance: `agents/lo2cin4bt_AcceptanceSubAgent.agent.md`
- Performance analysis: `agents/lo2cin4bt_PerformanceAnalysisSubAgent.agent.md`

## Matching Skills

- PM routing: `skills/lo2cin4bt-pm/SKILL.md`
- Teaching: `skills/lo2cin4bt-teaching/SKILL.md`
- Strategy building: `skills/lo2cin4bt-strategy-builder/SKILL.md`
- Backtesting: `skills/lo2cin4bt-backtesting/SKILL.md`
- Acceptance: `skills/lo2cin4bt-acceptance/SKILL.md`
- Performance analysis: `skills/lo2cin4bt-performance-analysis/SKILL.md`
- General project skill: `skills/lo2cin4bt/SKILL.md`
- Strategy Building Blocks verdict flow: `references/strategy-authoring-template.md`

## Required Routing

1. Read `agents/lo2cin4bt_PM.agent.md`.
2. Read `skills/lo2cin4bt-pm/SKILL.md`.
3. Classify the request.
4. Read only the selected agent/sub-agent and matching skill.
5. Use repo evidence before writing, running, accepting, or explaining anything.
6. For Strategy Building Blocks work, use `references/strategy-authoring-template.md` and return `supported`, `needs_clarification`, or `unsupported_needs_new_building_block`.

## Mode Boundaries

- Teacher explains setup, pages, terms, manuals, and next learning steps.
- Strategy building turns ideas into capability verdicts and supported config drafts.
- Backtesting runs or troubleshoots local backtest workflows.
- Acceptance checks requirements, schemas, docs, workspace boundaries, and evidence.
- Performance analysis explains generated artifacts and blocks unsupported claims.

Anything outside agents, skills, references, local docs, local configs, local backtests, or generated artifacts must be reported as outside the current agent/skill scope. Code changes require a bounded implementation task with tests. Live trading, order placement, fund movement, position changes, account-setting changes beyond read-only market-data setup, production deployment, legal advice, tax advice, and financial advice are out of scope.

## Safety Rules

- No live trading, order placement, fund movement, position changes, account-setting changes, or production deployment.
- Broker/exchange accounts may be configured only for read-only market-data access when the user explicitly asks for that setup.
- No invented config fields, strategy modes, metrics, provider behavior, or WFA conclusions.
- No runnable config for unsupported Strategy Building Blocks until code, tests, and quant safety metadata exist.
- Anti-look-ahead guardrails are mandatory: observation time, data availability time, earliest trade time, no future bars, no bfill for tradable signals, and WFA train/OOS separation.
- Missing fields are `not generated` or `not applicable`, never zero.
- Parameter Matrix winners are not WFA proof.
- WFA claims require generated selected-optimum/out-of-sample evidence and quant review when public wording changes.

## Beginner Prompt

Traditional Chinese:

```text
你現在是 lo2cin4bt/agents/lo2cin4bt_PM.agent.md。請先閱讀 agents/lo2cin4bt_PM.agent.md，並按它的指示讀取必要的 skills 和 docs。幫我開發 BTCUSDT 日線雙均線交易策略，其他參數用預設；只做本機回測，不要實盤交易。
```

English:

```text
You are lo2cin4bt/agents/lo2cin4bt_PM.agent.md. First read agents/lo2cin4bt_PM.agent.md, then follow its instructions to load the required skills and docs. Build a BTCUSDT daily dual-moving-average strategy with defaults; run only local backtesting, not live trading.
```

## Mandatory Notice

No lo2cin4bt agent or skill is investment advice, trading advice, financial advice, or an instruction to trade.
