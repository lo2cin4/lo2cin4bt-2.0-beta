# lo2cin4bt AI Skill + Lecture Guide

This guide tells AI assistants how to teach lo2cin4bt without overstating what the engine supports.

For live operating rules, load:

- `skills/lo2cin4bt/SKILL.md`
- `skills/lo2cin4bt/references/strategy-config-fields.md`
- `skills/lo2cin4bt/references/feature-recipes.md`
- `skills/lo2cin4bt/references/payload-contract-map.md`
- `skills/lo2cin4bt/references/quant-interpretation-risks.md`

## Teaching Principles

- Teach the current repo, not an imagined future framework.
- Show users how to work inside `workspace/`.
- Explain strategy configs through `strategy_run`, `computed_fields`, `signals`, `selection`, `allocation`, `rebalance`, and `fill_model`.
- Explain WFA through `wfa_run` that references a `workspace/runs/...json` strategy config.
- Do not teach reserved modes as runnable until code, schema, support checker, tests, and QuantReview confirm support.
- Do not imply that AI can understand arbitrary files without a contract.

## Beginner Flow

Beginner lessons should stay simple:

1. Install and start the local app.
2. Open or reuse `http://127.0.0.1:2424/`.
3. Use Run Center to run an existing `workspace/runs/` config.
4. Open Metrics, Parameter Matrix, Backtests, or WFA results.
5. Ask AI to summarize results and risks in plain language.

Do not put maintainer tools such as Bandit, coverage, Rust gates, or architecture audit in beginner flow. Those belong in advanced / maintainer modules.

## Strategy Authoring Flow

When a user gives a strategy idea, AI should:

1. Identify required data and whether it already exists.
2. Ask for missing files or assumptions.
3. Decide whether a feature contract or indicator extension is needed.
4. Draft a `strategy_run` config only after all required fields are known.
5. Run `workspace_doctor` before running a backtest.
6. Run a small smoke backtest before large Parameter Matrix or WFA jobs.
7. Review outputs using payloads and artifacts, not filenames.

## Custom Data And Look-Forward Safety

External data needs a `workspace/features/*.user.json` contract. Each feature must include `data_availability`:

- `observed_at`
- `usable_from`
- `point_in_time`
- `revision_policy`

If timing is unknown, AI must ask the user or mark the item for QuantReview. It must not assume a CSV column was known before the simulated trade.

## Custom Indicators

Custom indicators belong in:

```text
workspace/indicators/extensions/<slug>/manifest.json
workspace/indicators/extensions/<slug>/indicator.py
```

After writing one, run:

```powershell
python scripts/indicator_doctor.py workspace/indicators/extensions
```

If an indicator passes the doctor but no runtime dispatch supports it, AI must return `unsupported_needs_new_building_block` instead of creating a runnable config that pretends to work.

Teaching should keep the existing workspace design: users share the referenced `workspace/` files, not a new package format. Explain that normal data/config/WFA files can be reused when paths and contracts match, while custom indicators are code and need doctor validation plus runtime support.

## Advanced / Maintainer Flow

Advanced modules may teach:

- `python scripts/quality_gate.py --quick`
- `python scripts/quality_gate.py --full`
- template golden regression tests;
- frontend payload parity audit;
- public release audit;
- architecture audit;
- Bandit and dependency audit;
- Rust gates where Rust crates exist.

These are maintenance defenses, not things a first-time user needs to understand before the first backtest.

## No Live Trading

All teaching must keep lo2cin4bt as local research software. Do not instruct users to deploy, enable live trading, place orders, move funds, change positions, or change account settings. Broker and exchange accounts are market-data only unless a future explicitly reviewed feature changes that.
