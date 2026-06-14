# Backtester Overview

This folder is the canonical home for lo2cin4bt backtest runtime and backtest-specific contracts.

## Runtime Components
- `TradeSimulator_backtester.py`: fill and position simulation
- `NodeIRExecutor_backtester.py`: semantic strategy execution path for single-asset signal contracts
- `MultiAssetPortfolioEngine_backtester.py`: portfolio feature construction, allocation, accounting, costs, and validation
- `PortfolioInvariant_backtester.py`: optional account/portfolio consistency checks for cash, market value, equity, fees, positions, and trade records
- `RiskGate_backtester.py`: optional pre-trade portfolio risk gates for max positions, max order size, daily loss, and drawdown controls
- `Indicators_backtester.py`: indicator dispatch and signal generation
- `IndicatorManifestRegistry_backtester.py`: manifest loader (core aliases/modules + workspace extensions)

## Contract Canonical Root
- `backtester/contracts/strategy/`
- `backtester/contracts/feature/`
- `backtester/contracts/indicator-manifest/`

`docs/contracts` is index-only and no longer source-of-truth.

## User Extension Entry
- official user extension root: `workspace/indicators/extensions/`
- each extension package should include its own `manifest.json`
- Python implementations can be bound through `artifact_path + entrypoint`
- current design already supports multi-column extension params for future multi-factor logic

## Runtime Contract
- Public runs use `strategy_run`, NodeIR, or portfolio execution.
- `engine_mode` is limited to `auto` / `node_ir` for supported public runs.

## Notes
- Portfolio invariant checks are opt-in and independent from engine behavior. Existing result formats can be checked directly in tests; multi-asset results are currently converted in weight space because share-level cash ledger fields are not yet part of the result contract.
- Portfolio risk gates are opt-in under `risk.gates`. When disabled, backtests keep their existing accounting behavior. When enabled, target weights are adjusted before accounting and exported as `risk_gate_events` plus `risk_gate_summary`.
