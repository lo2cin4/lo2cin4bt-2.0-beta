# Contracts Migration Index

`docs/contracts` is now an index-only folder.

Backtest contract canonical root has moved to:
- `backtester/contracts/`

## New Canonical Structure
- `backtester/contracts/strategy/`
  - `strategy-contract.schema.json`
  - `examples/strategy-vix-regime-ma-cross.json`
- `backtester/contracts/feature/`
  - `feature-contract-v1.schema.json`
  - `examples/feature-contract-vix-price-v1.json`
- `backtester/contracts/indicator-manifest/`
  - `indicator-manifest-v1.schema.json`
  - `manifests/core/*.json`
  - `examples/indicator-manifest-ma-core-v1.json`

## Migration Mapping
- `docs/contracts/strategy-contract.schema.json` -> `backtester/contracts/strategy/strategy-contract.schema.json`
- `docs/contracts/feature-contract-v1.schema.json` -> `backtester/contracts/feature/feature-contract-v1.schema.json`
- `docs/contracts/indicator-manifest-v1.schema.json` -> `backtester/contracts/indicator-manifest/indicator-manifest-v1.schema.json`
- `docs/contracts/examples/*` -> corresponding `backtester/contracts/*/examples/*`
- `docs/contracts/manifests/core/*` -> `backtester/contracts/indicator-manifest/manifests/core/*`

## Legacy Policy
- Existing runtime now resolves contracts from `backtester/contracts` first.
- Legacy `docs/contracts` lookup remains temporary fallback for transition only.
- Draft archive notes are not part of the public 2.0.0 source tree.
