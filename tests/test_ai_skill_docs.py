from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path

from app.api.payloads import METRIC_KEY_MAP

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "lo2cin4bt"
REFERENCE_ROOT = SKILL_ROOT / "references"


DOC_COVERAGE_FILES = [
    "feature-recipes.md",
    "frontend-pages.md",
    "metric-dictionary.md",
    "payload-contract-map.md",
    "quant-interpretation-risks.md",
]

FRONTEND_TYPE_FIELDS = {
    "plotter/web/src/pages/WFAPage.tsx": [
        "WfaRow",
        "WfaPortfolioWeight",
        "WfaPortfolioContribution",
        "WfaPortfolioSnapshot",
        "WfaPortfolioWindowSummary",
        "WfaComboGroup",
    ],
    "plotter/web/src/pages/ParameterMatrixPage.tsx": [
        "HeatmapRow",
        "ShortlistRow",
        "ParameterImportanceRow",
        "ClusterSummaryRow",
        "StudySummary",
        "AcceptanceConfig",
        "RankingConfig",
        "RobustSelectionConfig",
        "FutureLiveSearchConfig",
        "ParameterReviewTemplate",
        "ParameterReviewTemplatePayload",
        "HeatmapPayload",
    ],
}

ACTIVE_RELEASE_DOCS = [
    "README.md",
    "README.en.md",
    "Troubleshooting.md",
    "docs/INSTALL.md",
    "docs/STRATEGY_BUILDING_BLOCKS.md",
    "docs/TUTORIAL.md",
    "docs/CHANGELOG.md",
    "docs/backtest-config-and-contracts.md",
    "docs/backtest-validation-status.md",
    "docs/contracts/README.md",
    "workspace/README.md",
    "skills/lo2cin4bt/references/quant-interpretation-risks.md",
    "skills/lo2cin4bt/references/workspace-and-github-boundary.md",
]

ALLOWED_PUBLIC_BRAND_TOKENS = {"lo2cin4bt", "LO2CIN4BT", "Lo2cin4BT"}

PUBLIC_BRAND_SCAN_ROOTS = [
    "AGENTS.md",
    "README.md",
    "README.en.md",
    "Troubleshooting.md",
    "main.py",
    "app",
    "autorunner",
    "backtester",
    "dataloader",
    "docs",
    "Lecture",
    "metricstracker",
    "plotter/web/index.html",
    "scripts",
    "skills",
    "statanalyser",
    "wfanalyser",
]

PUBLIC_BRAND_SCAN_EXTENSIONS = {
    ".cfg",
    ".html",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

FORBIDDEN_ACTIVE_DOC_STRINGS = [
    "docs/archive/",
    "records/autorunner",
    "outputs/backtester/",
    "outputs/metricstracker/",
    "outputs/wfanalyser/",
]

STALE_ZH_PUBLIC_COPY_TERMS = [
    "Public GitHub",
    "Run Center",
    "Strategy Performance",
    "Single Backtest",
    "Parameter Matrix",
    "AI Review Pack",
    "Command Center",
    "Factor Analysis",
    "workspace config",
    "app runtime",
    "outputs/app",
]

LEGACY_CORRUPTED_ZH_PUBLIC_LABELS = [
    "????",
    "???",
]


def _balanced_block_after_brace(text: str, brace_index: int) -> str:
    depth = 0
    for index, char in enumerate(text[brace_index:], brace_index):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace_index : index + 1]
    raise AssertionError("Unbalanced TypeScript block in frontend source")


def _typescript_type_block(source: str, type_name: str) -> str:
    marker = f"type {type_name} ="
    start = source.index(marker)
    brace_index = source.index("{", start)
    return _balanced_block_after_brace(source, brace_index)


def _typescript_const_object_block(source: str, const_name: str) -> str:
    start = source.index(f"const {const_name}")
    brace_index = source.index("= {", start) + 2
    return _balanced_block_after_brace(source, brace_index)


def _extract_type_fields(relative_path: str, type_name: str) -> set[str]:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    block = _typescript_type_block(source, type_name)
    return set(re.findall(r"(?:^|[;{\n])\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:", block))


def _extract_const_object_keys(relative_path: str, const_name: str) -> set[str]:
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    block = _typescript_const_object_block(source, const_name)
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\{", block, re.MULTILINE))


def _docs_corpus() -> str:
    return "\n".join((REFERENCE_ROOT / filename).read_text(encoding="utf-8") for filename in DOC_COVERAGE_FILES)


def _iter_public_brand_scan_files() -> list[Path]:
    files: list[Path] = []
    excluded_parts = {
        ".git",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "dist",
        "logs",
        "node_modules",
        "outputs",
        "tests",
    }
    for relative_root in PUBLIC_BRAND_SCAN_ROOTS:
        root = REPO_ROOT / relative_root
        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = [path for path in root.rglob("*") if path.is_file()]
        else:
            continue
        for path in candidates:
            relative_parts = set(path.relative_to(REPO_ROOT).parts)
            if relative_parts & excluded_parts:
                continue
            if path.suffix in PUBLIC_BRAND_SCAN_EXTENSIONS:
                files.append(path)
    return sorted(set(files))


def test_lo2cin4bt_skill_has_required_frontmatter_and_references() -> None:
    skill_path = SKILL_ROOT / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    assert "\nname: lo2cin4bt\n" in text
    assert "\ndescription: " in text
    assert "references/first-run.md" in text
    assert "references/metric-dictionary.md" in text
    assert "references/troubleshooting.md" in text
    assert "references/lo2cin4-agent-contract.md" in text
    assert "references/readme-acceptance-criteria.md" in text


def test_public_user_facing_brand_uses_lowercase_lo2cin4bt() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _iter_public_brand_scan_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"lo2cin4bt", text, flags=re.IGNORECASE):
            token = match.group(0)
            if token not in ALLOWED_PUBLIC_BRAND_TOKENS:
                offenders.append((str(path.relative_to(REPO_ROOT)), token))

    assert offenders == []

    required_refs = [
        "acceptance-criteria.md",
        "contracts-index.md",
        "feature-recipes.md",
        "first-run.md",
        "frontend-pages.md",
        "lo2cin4-agent-contract.md",
        "metric-dictionary.md",
        "payload-contract-map.md",
        "quant-interpretation-risks.md",
        "readme-acceptance-criteria.md",
        "strategy-config-fields.md",
        "troubleshooting.md",
        "workspace-and-github-boundary.md",
    ]
    for filename in required_refs:
        assert (SKILL_ROOT / "references" / filename).exists()


def test_metric_dictionary_covers_payload_metric_keys() -> None:
    dictionary = (SKILL_ROOT / "references" / "metric-dictionary.md").read_text(encoding="utf-8")

    missing = [key for key in METRIC_KEY_MAP if f"`{key}`" not in dictionary]
    assert missing == []


def test_teaching_docs_cover_frontend_public_payload_fields() -> None:
    fields = set(_extract_const_object_keys("plotter/web/src/pages/BacktestsPage.tsx", "KPI_META"))

    for relative_path, type_names in FRONTEND_TYPE_FIELDS.items():
        for type_name in type_names:
            fields.update(_extract_type_fields(relative_path, type_name))

    corpus = _docs_corpus()
    missing = sorted(field for field in fields if f"`{field}`" not in corpus)
    assert missing == []


def test_beginner_first_run_mentions_current_runtime_contract() -> None:
    first_run = (SKILL_ROOT / "references" / "first-run.md").read_text(encoding="utf-8")
    troubleshooting = (SKILL_ROOT / "references" / "troubleshooting.md").read_text(encoding="utf-8")

    for text in [first_run, troubleshooting]:
        assert "Python 3.12" in text
        assert "127.0.0.1:2424" in text
        assert "workspace/runs" in text
        assert "outputs/app" in text


def test_frontend_teaching_reference_covers_major_pages() -> None:
    pages = (SKILL_ROOT / "references" / "frontend-pages.md").read_text(encoding="utf-8")

    for page_name in [
        "Command Center",
        "Run Center",
        "Metrics",
        "Parameter Matrix",
        "Backtests",
        "WFA",
        "Factor Analysis",
    ]:
        assert page_name in pages


def test_release_packaging_boundaries_are_documented_and_guarded() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    for pattern in [
        "workspace/runs/**",
        "workspace/wfa/**",
        "!plotter/web/public/fonts/shippori-mincho/*.ttf",
        "!tests/fixtures/**/*.csv",
        "!verification/fixtures/**/*.json",
        "!assets/readme/logos/**/*.svg",
        "workspace/features/*.json",
        "workspace/strategies/*.json",
        "outputs/",
    ]:
        assert pattern in gitignore

    assert not (REPO_ROOT / "workspace" / "runs" / ".gitkeep").exists()
    assert not (REPO_ROOT / "workspace" / "wfa" / ".gitkeep").exists()
    assert (REPO_ROOT / "docs" / "RELEASE_NOTES_v2.0.0.md").exists()
    assert (REPO_ROOT / "docs" / "ROADMAP.md").exists()
    assert (REPO_ROOT / "docs" / "STRATEGY_BUILDING_BLOCKS.md").exists()

    for filename in ["TUTORIAL.md", "CHANGELOG.md"]:
        assert (REPO_ROOT / "docs" / filename).exists()

    building_blocks = (REPO_ROOT / "docs" / "STRATEGY_BUILDING_BLOCKS.md").read_text(encoding="utf-8")
    roadmap = (REPO_ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")

    for phrase in [
        "registry_supported",
        "unsupported_needs_new_building_block",
        "Look-Ahead Boundary",
        "revision_policy: revised_history",
    ]:
        assert phrase in building_blocks

    assert "Beginners do not need this document for the first run" in roadmap
    assert "Do not copy this whole roadmap into README" in roadmap


def test_readme_default_chinese_entry_and_english_article_are_marketing_pages() -> None:
    readme_zh = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_en = (REPO_ROOT / "README.en.md").read_text(encoding="utf-8")
    required_readme_assets = [
        REPO_ROOT / "assets" / "readme" / "hero" / "lo2cin4btneon.jpg",
        REPO_ROOT / "assets" / "readme" / "zh-Hant" / "01-overview.webp",
        REPO_ROOT / "assets" / "readme" / "zh-Hant" / "02-run-center.webp",
        REPO_ROOT / "assets" / "readme" / "en" / "01-overview.webp",
        REPO_ROOT / "assets" / "readme" / "en" / "02-run-center.webp",
        REPO_ROOT / "assets" / "readme" / "logos" / "yfinance.svg",
        REPO_ROOT / "assets" / "readme" / "logos" / "binance.svg",
        REPO_ROOT / "assets" / "readme" / "logos" / "coinbase.svg",
        REPO_ROOT / "assets" / "readme" / "logos" / "files.svg",
        REPO_ROOT / "assets" / "readme" / "logos" / "futu.svg",
        REPO_ROOT / "assets" / "readme" / "logos" / "futu-display.svg",
        REPO_ROOT / "assets" / "readme" / "logos" / "ibkr.svg",
    ]

    assert "README.en.md" in readme_zh
    assert "README.md" in readme_en
    assert "README.zh-Hant.md" not in readme_zh
    assert "README.zh-Hant.md" not in readme_en
    assert "Choose your language" not in readme_zh
    for asset in required_readme_assets:
        assert asset.exists(), asset
        assert asset.stat().st_size > 0, asset

    logo_sources = (REPO_ROOT / "assets" / "readme" / "logos" / "LOGO_SOURCES.md").read_text(
        encoding="utf-8"
    )
    assert "static.futunn.com/futuholdings/logo/futulogo.svg" in logo_sources
    assert "interactivebrokers.com/images/common/logos/ibkr/interactive-brokers.svg" in logo_sources
    assert "GitHub-safe display wrapper" in logo_sources

    futu_path = REPO_ROOT / "assets" / "readme" / "logos" / "futu.svg"
    futu_display_path = REPO_ROOT / "assets" / "readme" / "logos" / "futu-display.svg"
    ibkr_path = REPO_ROOT / "assets" / "readme" / "logos" / "ibkr.svg"
    futu_logo = futu_path.read_text(encoding="utf-8")
    futu_display = futu_display_path.read_text(encoding="utf-8")
    ibkr_logo = ibkr_path.read_text(encoding="utf-8")
    for logo_path in [futu_path, futu_display_path, ibkr_path]:
        raw = logo_path.read_bytes()
        assert b"\r\n" not in raw, logo_path
        assert raw.endswith(b"\n"), logo_path
        ET.fromstring(raw.decode("utf-8"))

    assert sha256(futu_path.read_bytes()).hexdigest() == (
        "fbedea03e987c01c6451cf3787013142478eee80a8012c49e4b674559383da6e"
    )
    assert sha256(ibkr_path.read_bytes()).hexdigest() == (
        "6d0477a0bf6acfea26c71246edf965880228b2c38a6b274ff28ec07bebc7fc63"
    )
    assert "viewBox=\"0 0 816 184\"" in futu_logo
    assert "fill=\"#FFFFFF\"" in futu_logo
    assert 'href="data:image/svg+xml;base64,' in futu_display
    assert 'href="futu.svg"' not in futu_display
    assert 'fill="#111827"' in futu_display
    assert "assets/readme/logos/futu-display.svg" in readme_zh
    assert "assets/readme/logos/futu-display.svg" in readme_en
    inline_background_sentinel = "background:" + "#111827"
    assert inline_background_sentinel not in readme_zh
    assert inline_background_sentinel not in readme_en
    escaped_newline_sentinel = "`" + "n"
    style_attribute_sentinel = "style" + "="
    class_attribute_sentinel = "class" + "="
    enable_background_sentinel = "enable" + "-background"
    assert "viewBox=\"0 0 452 69\"" in ibkr_logo
    assert style_attribute_sentinel not in ibkr_logo
    assert class_attribute_sentinel not in ibkr_logo
    assert enable_background_sentinel not in ibkr_logo
    assert 'stop-color="#D81222"' in ibkr_logo
    assert escaped_newline_sentinel not in ibkr_logo

    for article in [readme_zh, readme_en]:
        for link in [
            "docs/TUTORIAL.md",
            "docs/INSTALL.md",
            "Troubleshooting.md",
        ]:
            assert link in article
        for phrase in [
            "workspace/",
            "skills/lo2cin4bt/SKILL.md",
            "docs/ai/AI_MANUAL_SKILL.md",
            "docs/ai/AI_SKILL_LECTURE_GUIDE.md",
        ]:
            assert phrase in article

    for phrase in [
        "assets/readme/zh-Hant/01-overview.webp",
        "assets/readme/zh-Hant/02-run-center.webp",
        "https://youtu.be/XIPYRn3H0tU?si=5RoLzrmGLEG6uxaD",
    ]:
        assert phrase in readme_zh

    for phrase in [
        "assets/readme/en/01-overview.webp",
        "assets/readme/en/02-run-center.webp",
        "https://youtu.be/03CduKFc4sg?si=GE7Y2EFKnsiF3HFV",
        "does not support order placement",
    ]:
        assert phrase in readme_en

    for forbidden in [
        "strategy_run" + "." + "v2",
        "wfa_run" + "." + "v2",
        "next_bar_after_signal",
        "signal_close_for_next_bar",
        "Public GitHub",
        "outputs/app",
        "????",
    ]:
        assert forbidden not in readme_zh
        assert forbidden not in readme_en


def test_readme_beginner_accessibility_copy_has_no_hidden_references() -> None:
    readme_zh = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_en = (REPO_ROOT / "README.en.md").read_text(encoding="utf-8")
    install = (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")

    for phrase in [
        "你現在是 lo2cin4 的 PM",
        "派 sub agent",
        "只做本機回測，不要實盤交易",
        "工作區檢查",
    ]:
        assert phrase in readme_zh

    for phrase in [
        "You are the PM for lo2cin4",
        "Delegate to sub agents",
        "Practical safety checks",
    ]:
        assert phrase in readme_en

    for forbidden in [
        "Inspired by CCXT",
        "CCXT's exchange table style",
        "Paste the starter prompt above",
        "download Futubull-register/login",
        "????",
    ]:
        assert forbidden not in "\n".join([readme_zh, readme_en])

    for phrase in [
        "download Futubull",
        "AZ57KU",
        "read-only market data",
        "Do not enable trading",
    ]:
        assert phrase in install


def test_readme_webp_and_youtube_visual_assets_match_readme_contract() -> None:
    from PIL import Image

    readme_text = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "README.en.md").read_text(encoding="utf-8"),
        ]
    )
    linked_webp_assets = set(
        re.findall(r"!\[[^\]]*]\((assets/readme/(?:zh-Hant|en)/[^)]+\.webp)\)", readme_text)
    )
    expected_webp_assets = {
        "assets/readme/zh-Hant/01-overview.webp",
        "assets/readme/zh-Hant/02-run-center.webp",
        "assets/readme/en/01-overview.webp",
        "assets/readme/en/02-run-center.webp",
    }

    assert "assets/readme/showcase/" not in readme_text
    assert "assets/readme/full/" not in readme_text
    assert "assets/readme/scroll/" not in readme_text
    assert "docs/assets/readme/" not in readme_text
    assert "https://youtu.be/XIPYRn3H0tU?si=5RoLzrmGLEG6uxaD" in readme_text
    assert "https://youtu.be/03CduKFc4sg?si=GE7Y2EFKnsiF3HFV" in readme_text
    assert expected_webp_assets.issubset(linked_webp_assets)

    for relative_path in expected_webp_assets:
        asset_path = REPO_ROOT / relative_path
        assert asset_path.exists(), relative_path
        with Image.open(asset_path) as image:
            assert image.width >= 900, relative_path
            assert image.height >= 500, relative_path
            assert image.format == "WEBP", relative_path


def test_active_release_docs_avoid_stale_public_paths() -> None:
    offenders: list[tuple[str, str]] = []

    for relative_path in ACTIVE_RELEASE_DOCS:
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_ACTIVE_DOC_STRINGS:
            if forbidden in text:
                offenders.append((relative_path, forbidden))

    assert offenders == []


def test_broker_docs_are_optional_market_data_not_first_run_or_live_trading() -> None:
    corpus = "\n".join(
        [
            (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "ai" / "AI_SKILL_LECTURE_GUIDE.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "Troubleshooting.md").read_text(encoding="utf-8"),
            (SKILL_ROOT / "references" / "first-run.md").read_text(encoding="utf-8"),
        ]
    )

    for phrase in [
        "not part of the first run",
        "read-only market data",
        "market-data",
        "does not place live orders",
    ]:
        assert phrase in corpus
