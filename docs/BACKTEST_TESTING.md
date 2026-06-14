# Backtest Testing Guide

This project already has a deterministic smoke path for checking whether the
backtest pipeline still works.

## Quick Health Check

Run the smoke tier only:

```bash
pytest -m smoke -q
```

This is the fastest way to verify:

- config validation still works
- autorunner config loading still works
- file-based data loading still works
- the backtest engine still produces trade results

## Full Phase 1 Regression

Run everything:

```bash
pytest -q
```

## Test Markers

- `smoke`: the shortest end-to-end checks
- `golden`: deterministic snapshot coverage
- `regression`: contract and edge-case coverage

## Coverage Map

For the public quality gate summary, see `docs/QUALITY_GATES.md`. Internal
refactor notes and one-off migration packs are not required for normal users.

## What To Watch

If `smoke` fails, the backtest pipeline is no longer trustworthy enough for
normal use. Check the smoke test first, then inspect the config, loader, engine,
and exporter layers in that order.
