# Roadmap

This roadmap is a planning document, not a promise of delivery dates. The owner
decides release priority. PM should map meaningful user-facing changes to MINOR
versions and reserve MAJOR changes for future compatibility breaks.

Beginners do not need this document for the first run. Do not copy this whole roadmap into README.

## Current Baseline

`2.0.0` is the public GitHub baseline:

- browser app;
- structured strategy configs;
- single backtest, Parameter Matrix, WFA, and rolling validation;
- portfolio accounting and contribution diagnostics;
- AI-readable result packs;
- repo-local Codex skill and teaching references.

## Near-Term PATCH Work

PATCH releases should stay small:

- copy and typo fixes;
- docs clarity improvements;
- CI/test fixes;
- bug fixes that do not change user-facing capability;
- small compatibility fixes for setup, fixtures, or packaging.

Example versions: `2.0.1`, `2.0.2`, `2.0.13`.

## Candidate MINOR Work

These are meaningful user-facing improvements and should normally become
`2.x.0` releases.

### 2.1 Candidate: Beginner Config Experience

- Curated public starter configs if the owner approves publishing examples.
- Guided config generator for first local backtest.
- Better Run Center empty-state actions.
- Safer public sample data policy.

### 2.2 Candidate: Strategy Authoring UX

- Visual config helper for `strategy_run`.
- Schema-guided field picker for strategies, features, allocation, and
  rebalance rules.
- Validation messages that point directly to docs.
- More strategy recipes for calendar overlays and rotation policies.

### 2.3 Candidate: Result Review Workflow

- Built-in result review checklist.
- AI review pack viewer in the frontend.
- Stronger stale-artifact warnings.
- Side-by-side benchmark/result comparison.

### 2.4 Candidate: WFA And Robustness

- More WFA diagnostics for window stability.
- Parameter stability visualizations.
- Better separation of selected rows and diagnostic candidates.
- Portfolio WFA drill-down improvements.

### 2.5 Candidate: Factor Research Preview

- IC and Rank IC diagnostics.
- Factor cleaning and neutralization workflow.
- Factor portfolio construction preview.
- Explicit point-in-time and survivorship warnings for factor datasets.

## Larger Future Work

These may require a later MINOR or MAJOR decision:

- richer data lineage manifests across all providers;
- multi-provider symbol normalization;
- more formal plugin/extension API;
- public example gallery and docs site;
- local-only scheduled experiment runner;
- deeper Rust accounting integration;
- broader cross-platform packaging checks.

## Out Of Scope For 2.x Baseline

- live trading;
- broker order placement;
- external account modification;
- deployment as a hosted trading service;
- guarantees of investment performance.

## Release Discipline

- Update `docs/CHANGELOG.md` when public behavior changes.
- Update release notes for user-facing tags.
- Run the quality gates before sharing a build.
- Keep runtime outputs and local workspace configs out of Git unless the owner
  explicitly approves a curated example.
