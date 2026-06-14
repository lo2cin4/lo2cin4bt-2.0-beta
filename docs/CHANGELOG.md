# Changelog

All notable project changes should be recorded here.

Version policy follows `MAJOR.MINOR.PATCH`:

- PATCH: bug fixes, typo fixes, docs corrections, test-only fixes.
- MINOR: meaningful user-facing feature changes.
- MAJOR: large compatibility-breaking changes.

## 2.0.2 - Version Badge And Windows Shortcut

Release type: patch release for the public beta.

### Added

- Added an in-app sidebar version badge showing `version: 2.0.2 beta`.
- Added README update instructions for users who already cloned the repo.
- Added optional Windows desktop shortcut scripts.
- Added a lo2cin4bt desktop icon asset for the Windows shortcut.

## 2.0.1 - Metrics Page Rendering Fix

Release type: patch release for the public beta.

### Fixed

- Fixed blank metrics, WFA, parameter matrix, and backtest pages caused by
  Plotly React component loading in production browser bundles.
- Updated the public beta release version after the rendering fix.

### Validation

- Frontend tests and production build passed.
- Browser route verification confirmed metrics and related pages render.
- GitHub Actions passed: CI, CodeQL, and Semgrep.

## 2.0.0 - Public Baseline

Release type: initial public GitHub baseline for the 2.x line.

### Added

- Browser-first FastAPI + React app at `http://127.0.0.1:2424/`.
- Run Center for local backtest and WFA batch execution.
- Strategy Performance page for ranking and filtering strategy rows.
- Backtests page with equity, benchmark, drawdown, trade/event rows, holdings,
  allocation changes, asset contribution, turnover, costs, and risk diagnostics.
- Parameter Matrix for structured `parameter_domains`.
- WFA dashboard with selected optimum rows and OOS evidence separation.
- Rolling validation path for fixed strategies.
- AI-readable review packs under `outputs/app/ai_review/{run_id}/`.
- Repo-local Codex skill under `skills/lo2cin4bt/`.
- Release guard tests for skill docs, frontend field coverage, stale public docs,
  workspace boundaries, and broker safety wording.
- Bundled Shippori Mincho frontend font files for local runtime consistency.

### Changed

- Replaced older UI/runtime flow with the app-managed `outputs/app/` contract.
- Moved active public config style to `strategy_run`.
- Standardized strategy labels for strategy tables.
- Clarified benchmark labels such as same-symbol buy-and-hold vs explicit SPY.
- Improved frontend load performance through payload compression/cache and
  route-level UI code splitting.
- Updated workspace policy: user configs and datasets are local or distributed
  outside GitHub by default.

### Removed From Public Release

- Public legacy vector/sequential plotter runtime surfaces.
- Tracked local workspace strategy/feature JSON files.
- Runtime output snapshots and generated chart payloads.
- Old root `assets/` CSS files that were no longer part of the active frontend.

### Safety Notes

- Lo2cin4BT remains local research tooling.
- No result authorizes live trading.
- FUTU / IBKR support is optional market-data gateway work only; no order
  placement workflow is part of this release.
- Old artifacts that lack current fields should be rerun, not mixed into final
  validation evidence.

See also:

- `docs/RELEASE_NOTES_semantic.0.0.md`
- versioning guidance
- `docs/ROADMAP.md`
