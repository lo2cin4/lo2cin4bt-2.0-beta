"""Preview helper for semantic strategy contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

try:  # pragma: no cover - import fallback for script mode
    from .StrategyContractValidator import StrategyContractValidator
except ImportError:  # pragma: no cover
    from StrategyContractValidator import StrategyContractValidator
from utils.path_resolver import resolve_input_path


class StrategyPreview:
    """Provide a deterministic preview before running backtests."""

    def __init__(self) -> None:
        self.validator = StrategyContractValidator()

    def preview(
        self,
        strategy_contract_path: str,
        feature_contract_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_feature_path = feature_contract_path or self._resolve_feature_contract_ref(
            strategy_contract_path
        )
        result = self.validator.validate_file_paths(
            strategy_contract_path=strategy_contract_path,
            feature_contract_path=resolved_feature_path,
        )
        return {
            "valid": result.valid,
            "errors": result.errors,
            "warnings": result.warnings,
            "summary": result.summary,
            "resolved_feature_contract_path": resolved_feature_path,
        }

    @staticmethod
    def _resolve_feature_contract_ref(strategy_contract_path: str) -> Optional[str]:
        strategy_path = Path(strategy_contract_path)
        strategy_data = json.loads(strategy_path.read_text(encoding="utf-8-sig"))
        if not isinstance(strategy_data, dict):
            return None

        data_context = strategy_data.get("data_context", {})
        if not isinstance(data_context, dict):
            return None

        ref = data_context.get("feature_contract_ref")
        if not isinstance(ref, str) or not ref.strip():
            return None

        repo_root = Path(__file__).resolve().parents[1]
        resolved = resolve_input_path(
            ref,
            repo_root=repo_root,
            config_file_path=strategy_contract_path,
            legacy_roots=[repo_root / "docs" / "contracts"],
        )
        return str(resolved.path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview strategy.contract before running a backtest"
    )
    parser.add_argument(
        "--strategy",
        required=True,
        help="Path to strategy.contract JSON file",
    )
    parser.add_argument(
        "--feature",
        required=False,
        default=None,
        help="Optional path to feature contract JSON file",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    preview = StrategyPreview().preview(args.strategy, args.feature)
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    return 0 if preview["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
