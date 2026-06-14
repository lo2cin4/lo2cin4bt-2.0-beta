#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

INSTALL_DEV=0
INSTALL_BROKERS=0
SKIP_FRONTEND=0

for arg in "$@"; do
  case "$arg" in
    --dev) INSTALL_DEV=1 ;;
    --brokers) INSTALL_BROKERS=1 ;;
    --skip-frontend) SKIP_FRONTEND=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -q --disable-pip-version-check --upgrade pip wheel setuptools
.venv/bin/python -m pip install -q --disable-pip-version-check --require-hashes -r requirements.lock

if [ "$INSTALL_DEV" -eq 1 ]; then
  .venv/bin/python -m pip install -q --disable-pip-version-check --require-hashes -r requirements-dev.lock
fi

if [ "$INSTALL_BROKERS" -eq 1 ]; then
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements-brokers.txt
fi

if [ "$SKIP_FRONTEND" -eq 0 ]; then
  (cd plotter/web && npm ci && npm run build)
fi

.venv/bin/python scripts/doctor.py
