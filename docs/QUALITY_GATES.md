# Quality Gates

This project uses several checks. Each check has one job.

## One Command

For normal development, run one command:

```powershell
python scripts/quality_gate.py --quick
```

Before a public release, run:

```powershell
python scripts/quality_gate.py --full
```

The older commands below remain available for debugging one gate at a time.

| Gate | Tool | Purpose |
| --- | --- | --- |
| Environment | `python scripts/doctor.py` | Confirms local folders and basic dependencies exist. |
| Public release audit | `python scripts/public_release_audit.py` | Confirms public release docs, CI hooks, Lecture pages, and safety wording are present. |
| Total quality gate | `python scripts/quality_gate.py --quick` | Runs the normal fast checks through one entrypoint. |
| Pre-release audit | `python scripts/pre_release_maintenance_audit.py --quick` | Underlying audit runner used by the total gate; writes Markdown/JSON reports. |
| Architecture audit | `python scripts/architecture_audit.py --format text` | Blocks simple architecture smells such as engine code importing app code. |
| Consistency gate | `python verification/scripts/run_consistency_gate.py` | Checks project contracts, formatting, tests, and fixture consistency. |
| Python tests | `pytest` | Confirms selected behavior still works. |
| Coverage baseline | `pytest-cov` | Fails if the release test set touches too little code. |
| Python security | `bandit` | Blocks high-confidence security smells in Python code. |
| Dependency audit | `pip-audit`, `npm audit` | Blocks known high-risk dependency issues for Python and frontend packages. |
| Rust crate gates | `cargo fmt`, `cargo check`, `cargo test`, `cargo clippy` | Confirms optional Rust crates format, compile, test, and lint. |
| Quant oracle | oracle / pseudo-fuzz / golden tests | Checks timing, cost, slippage, deterministic fixtures, and accounting drift. |
| Template golden regression | `pytest tests/test_template_golden_regression.py` | Runs public strategy templates on fixed local data and fails if key results drift. |

## Coverage Baseline

The public release coverage gate is `50%` for the full pytest suite. The focused core backtest gate is `80%` for the multi-asset portfolio engine path.

Core 80% target: core backtest accounting paths have a focused `80%` coverage gate. This is not a promise that the whole repo is already 80% covered.

## GitHub Summary

`scripts/pre_release_maintenance_audit.py` writes Markdown and JSON reports locally. In GitHub Actions, it also appends the Markdown report to `GITHUB_STEP_SUMMARY`.

## Quant Boundary

Passing these gates means the software mechanics are checked. It does not prove a strategy is profitable.
