from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

LABEL_FALLBACK_LOCATIONS = [
    ROOT / "plotter" / "web" / "src" / "router.tsx",
    ROOT / "plotter" / "web" / "src" / "pages" / "WFAPage.tsx",
]


def main() -> int:
    errors: list[str] = []
    notes: list[str] = []

    panel_path = ROOT / "plotter" / "web" / "src" / "components" / "StrategyRulesPanel.tsx"
    panel_text = panel_path.read_text(encoding="utf-8")
    if "summary?.entry_rule" in panel_text and "execution_label" in panel_text:
        errors.append(
            f"{panel_path.relative_to(ROOT).as_posix()}: StrategyRulesPanel must not infer execution_label from entry_rule"
        )

    matrix_path = ROOT / "plotter" / "web" / "src" / "pages" / "ParameterMatrixPage.tsx"
    matrix_text = matrix_path.read_text(encoding="utf-8")
    for token in ("inferDatasetLabel", "extractAssetLabelFromRunLabel"):
        if token in matrix_text:
            errors.append(
                f"{matrix_path.relative_to(ROOT).as_posix()}: ParameterMatrixPage must use payload.dataset_label, not {token}"
            )

    for path in LABEL_FALLBACK_LOCATIONS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        markers = []
        for token in ("labelFromStructuredName", "formatRunLabel", "inferDatasetLabel", "extractAssetLabelFromRunLabel"):
            if token in text:
                markers.append(token)
        if markers:
            if "labelFromStructuredName" in markers and "legacyLabelFallbackAllowed" not in text:
                errors.append(
                    f"{path.relative_to(ROOT).as_posix()}: filename label fallback must be guarded by legacyLabelFallbackAllowed"
                )
                continue
            notes.append(f"{path.relative_to(ROOT).as_posix()}: label fallback markers: {', '.join(markers)}")

    backend_labels_path = ROOT / "app" / "runtime" / "labels.py"
    backend_labels = backend_labels_path.read_text(encoding="utf-8")
    if "is_strategy_run" not in backend_labels:
        errors.append(
            f"{backend_labels_path.relative_to(ROOT).as_posix()}: backend labels must separate strategy_run payload labels from legacy filename fallbacks"
        )
    forbidden_backend_patterns = [
        "_extract_strategy_run_asset(raw_config) or _extract_asset",
        "factor_semantic or _extract_factor",
        "strategy or _extract_strategy",
        "_extract_strategy_run_mode(raw_config, workflow) or _extract_mode",
    ]
    for token in forbidden_backend_patterns:
        if token in backend_labels:
            errors.append(
                f"{backend_labels_path.relative_to(ROOT).as_posix()}: strategy_run labels must not fall back to filename/path inference via {token}"
            )

    if notes:
        print("Frontend payload parity audit notes:")
        for note in notes:
            print(f"- {note}")

    if errors:
        print("Frontend payload parity audit failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Frontend payload parity audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
