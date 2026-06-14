# Workspace And GitHub Boundary

Use this before telling a user what will or will not upload to GitHub.

## Tracked Source Files

These files are normally safe to track in the shared source branch and push to
GitHub:

- Source code.
- Tests.
- Contracts and schemas.
- Public documentation.
- Setup scripts.
- `workspace/README.md`.
- Reviewed example indicator extension source under
  `workspace/indicators/extensions/`.
- The repo-local skill under `skills/lo2cin4bt/`.
- Stable public test and verification fixtures under `tests/fixtures/` and
  `verification/fixtures/`.
- Reviewed README visual assets under `assets/readme/`.

## Local Or Private Only

These files should stay local or private unless the owner explicitly decides to
share them. They may still be kept in a local-only Git repo, private branch,
private remote, or external archive if the owner wants Git history for them.

- `outputs/`
- `logs/`
- `plotter/web/dist/`
- `plotter/web/node_modules/`
- `.venv/`
- Python/TypeScript caches.
- `.env`, broker keys, private certs, sqlite/db files.
- `workspace/runs/**`
- `workspace/wfa/**`
- `workspace/datasets/**`
- `workspace/calendars/**`
- private or unreviewed `workspace/indicators/extensions/**`
- `workspace/features/*.json`
- `workspace/strategies/*.json`
- `workspace/statanalyser/**`
- release-excluded planning or archive documentation folders
- maintainer-only branch, publication, release-checklist, refactor, capture,
  or phase-plan notes unless explicitly promoted to reviewed public docs.

Included strategy and WFA examples live under `backtester/contracts/strategy/examples/`. On first app launch, lo2cin4bt copies those examples into the ignored local `workspace/runs/` and `workspace/wfa/` folders without overwriting existing user files. Runnable WFA configs must reference their strategy config with an explicit repo-relative `workspace/runs/<strategy-config>.json` path, not a bare filename. Extra private configs may still be distributed outside GitHub when needed.

Sharing a workspace strategy means sharing the run config and every referenced dataset, feature contract, WFA config, and reviewed indicator extension at the same relative paths. `workspace/indicators/extensions/**` contains executable code. Unreviewed custom indicators should stay local/private and must pass `indicator_doctor` plus runtime support before an AI agent may call the strategy runnable.

## README Visual Assets

Public README screenshots and animations must live under `assets/readme/`.
Do not store public README media under `outputs/`, `plotter/web/dist/`,
`workspace/`, root `assets/`, or any release-excluded docs folder.

Screenshots and GIFs must be produced from deterministic demo/synthetic
evidence. They must not show:

- `.env`, tokens, API keys, broker credentials, account IDs, certificates,
  account balances, order tickets, or live broker screens
- local absolute filesystem paths
- local datasets, private configs, private reports, generated runtime output
  inventories, raw run snapshots, raw chart payload JSON, or AI review packs
- release-excluded planning or archive documentation folders
- claims that backtest, Parameter Matrix, or WFA screenshots prove future
  returns, strategy validity, broker readiness, or live-trading safety

## Runtime Output Meaning

- `outputs/app/run_snapshots/` stores local run snapshots and managed artifacts.
- `outputs/app/chart_payloads/` stores frontend payload JSON.
- `outputs/app/ai_review/` stores AI-readable evidence packs.

These are generated evidence, not source.

## Staging Warning

If a file was tracked before `.gitignore` changed, it may still be tracked.
Removing it from future GitHub uploads requires `git rm --cached <path>` or an
explicit staging decision. Do not run destructive cleanup without user approval.

Git and GitHub are not separate visibility layers inside one pushed branch. A
file committed to the branch that is pushed to GitHub will be downloadable from
GitHub even if it later appears in `.gitignore`. `.gitignore` only protects
untracked files from accidental staging.
