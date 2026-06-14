# Semantic-Native Coding Contract

This document defines the stricter coding zone for the semantic-native backtest path.

## Scope
This contract currently applies to:

- `autorunner/FeatureContractValidator_v1.py`
- `autorunner/StrategyCompiler.py`
- `backtester/FeatureContractMaterializer_backtester.py`
- `backtester/NodeIRExecutor_backtester.py`
- `backtester/TradeRecordExporter_backtester.py`

## Goals
- keep semantic-native runtime behavior deterministic
- keep multi-source feature handling auditable
- reduce accidental drift between semantic runtime, exports, and tests

## Rules
1. Prefer contract-driven fields over ad-hoc fallback fields.
2. New semantic-native fields must be added to schema, validator, and runtime together.
3. Do not silently degrade semantic-native runtime into legacy bridge behavior.
4. Export naming for semantic/native outputs must stay semantic and consistent across CSV / Excel / parquet.
5. Multi-source joins must fail loudly on ambiguous keys rather than silently duplicating rows.
6. Non-ISO source timestamp parsing must be explicit in the feature contract when needed.
7. Avoid wildcard imports, undefined names, and unsorted imports in this zone.
8. Avoid `print()` in production modules in this zone; use structured logger / existing UI helpers instead.
9. New multi-source auditability fields must define machine-readable output placement through the audit-output contract.
10. Primary user-facing outputs must stay summary-oriented; detailed provenance belongs in sidecar metadata or audit artifacts.
11. When adding new audit fields, keep a small summary index in primary outputs and move detailed feature lineage into `*_audit.json` or `*_audit.parquet`.

## Required Verification
At minimum, changes in this zone should pass:

```bash
python verification/scripts/run_consistency_gate.py
python -m pytest -q tests/test_audit_output_contract_v1.py tests/test_feature_contract_validator_v1.py tests/test_feature_contract_materializer_backtester.py tests/test_node_ir_executor_backtester.py tests/test_wfa_e2e_smoke.py
```

## Notes
- This is not yet a whole-repo coding contract.
- The current intent is to harden the highest-risk semantic-native runtime slice first.
