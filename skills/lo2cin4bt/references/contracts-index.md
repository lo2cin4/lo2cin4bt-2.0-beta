# Contracts Index

Use this to find schemas and contract docs before creating or interpreting configs.

## Main Docs

- `README.md`: supported strategy types, runtime map, quick start.
- `docs/backtest-architecture.md`: high-level architecture.
- `docs/backtest-config-and-contracts.md`: config/input roots and contract notes.
- `docs/contracts/strategy-mode-and-workflow-contract.md`: strategy mode and workflow meaning.
- `docs/app-core-contracts.md`: app payload and truth-source rules.
- `docs/ai/AI_READABLE_OUTPUT_CONTRACT.md`: AI review pack boundary.

## Strategy And Feature Schemas

- `backtester/contracts/strategy/strategy-run.schema.json`
- `backtester/contracts/strategy/factor-pipeline-v1.schema.json`
- `backtester/contracts/strategy/examples/`
- `backtester/contracts/indicator-manifest/`
- `app/contracts/`

## Source Code Authorities

- `backtester/StrategyRunConfig_backtester.py`: config normalization and planning.
- `backtester/UnifiedBacktestRunner_backtester.py`: unified execution route.
- `backtester/MultiAssetPortfolioEngine_backtester.py`: portfolio accounting.
- `wfanalyser/UnifiedPortfolioWFARunner_wfanalyser.py`: portfolio WFA.
- `app/api/payloads.py`: frontend/API payload construction.
- `plotter/web/src/pages/`: frontend page consumers.

## Tests To Consult

- `tests/test_strategy_run_config.py`
- `tests/test_strategy_compiler_autorunner.py`
- `tests/test_app_portfolio_payloads.py`
- `tests/test_app_api_payloads.py`
- `tests/test_unified_portfolio_wfa_runner.py`
- `tests/test_wfa_bridge.py`
- `tests/test_calendar_event_strategy_backtester.py`
- `tests/test_risk_gate_backtester.py`
- `tests/test_portfolio_invariant_backtester.py`
