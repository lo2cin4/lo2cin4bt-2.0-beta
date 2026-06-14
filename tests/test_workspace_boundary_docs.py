from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_beginner_workspace_boundary_is_visible_in_public_readmes() -> None:
    readme_zh = _read("README.md")
    readme_en = _read("README.en.md")
    public_readmes = "\n".join([readme_zh, readme_en])

    for phrase in [
        "workspace/",
        "workspace/runs/",
        "do not run live trading",
        "not financial advice",
        "does not support order placement",
    ]:
        assert phrase in public_readmes

    private_company_root = "D:" + "\\Company"
    assert private_company_root not in public_readmes
    assert "outputs/app" not in public_readmes


def test_workspace_readme_declares_user_editable_boundary() -> None:
    workspace_readme = _read("workspace/README.md")

    for phrase in [
        "This folder is the only user input entrypoint.",
        "`workspace/datasets/`",
        "`workspace/features/`",
        "`workspace/indicators/`",
        "`workspace/strategies/`",
        "`workspace/runs/`",
        "`workspace/wfa/`",
        "Public GitHub releases keep runnable source examples",
        "Run Center also lists any JSON files you add locally",
        "do not use a bare filename",
        "ignored by Git by default",
        "workspace/indicators/extensions/<package>/manifest.json",
        "workspace/indicators/extensions/<package>/indicator.py",
    ]:
        assert phrase in workspace_readme


def test_public_readme_data_source_links_remain_read_only() -> None:
    readme_zh = _read("README.md")
    readme_en = _read("README.en.md")
    public_readmes = "\n".join([readme_zh, readme_en])

    for phrase in [
        "market data",
        "實盤交易軟件",
        "order-routing system",
        "does not support order placement",
    ]:
        assert phrase in public_readmes

    for forbidden in [
        "20% spot and 10% futures discount",
        "account-opening offer",
        "live trading enabled",
        "place broker order",
        "enable fund movement",
    ]:
        assert forbidden not in public_readmes.lower()


def test_workspace_runtime_inputs_are_gitignored_by_default() -> None:
    gitignore = _read(".gitignore")

    for pattern in [
        "workspace/calendars/**",
        "workspace/datasets/**",
        "workspace/features/**",
        "workspace/runs/**",
        "workspace/wfa/**",
        "workspace/strategies/**",
        "workspace/statanalyser/**",
        "workspace/reports/pre_release/",
    ]:
        assert pattern in gitignore

    for exception in [
        "!workspace/README.md",
        "!workspace/indicators/extensions/**/*.py",
        "!workspace/indicators/extensions/**/manifest.json",
    ]:
        assert exception in gitignore
