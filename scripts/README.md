# Scripts

Runnable repo helpers live here. Keep one-off or operator-facing commands in
this directory instead of creating a new top-level utility folder.

## Install And Health

- `setup.ps1`: Windows setup wrapper. Installs runtime dependencies, optionally
  dev and broker profiles, builds `plotter/web`, then runs `doctor.py`.
- `setup.sh`: macOS/Linux setup wrapper with the same behavior as `setup.ps1`.
- `doctor.py`: lightweight local setup check for Python, Node/npm, lockfiles,
  workspace example folders, and built frontend assets.

## Quality Utilities

- `quality_gate.py`: one entrypoint for the normal public quality checks.
- `public_release_audit.py`: checks that the repository surface has the
  required docs, workflows, and safety wording.
- `project_consistency_audit.py`: checks naming and payload consistency.
