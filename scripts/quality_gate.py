from __future__ import annotations

import argparse
import sys

from pre_release_maintenance_audit import main as pre_release_main


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Single entrypoint for lo2cin4bt quality checks."
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run the default fast gate for normal local changes.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run the full release gate with coverage, security, frontend, and Rust checks.",
    )
    args, passthrough = parser.parse_known_args()

    forwarded = ["pre_release_maintenance_audit.py"]
    if args.full:
        forwarded.append("--full")
    else:
        forwarded.append("--quick")
    forwarded.extend(passthrough)

    original_argv = sys.argv
    try:
        sys.argv = forwarded
        return pre_release_main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
