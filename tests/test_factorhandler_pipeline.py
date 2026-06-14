import sys
from pathlib import Path

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _factor_frames():
    dates = pd.date_range("2024-01-02", periods=8, freq="B")
    close = pd.DataFrame(
        {
            "AAA": [100, 101, 102, 103, 104, 105, 106, 107],
            "BBB": [100, 102, 104, 106, 108, 110, 112, 114],
            "CCC": [100, 99, 98, 97, 96, 95, 94, 93],
            "DDD": [100, 101, 101, 102, 102, 103, 103, 104],
        },
        index=dates,
    )
    market_cap = pd.DataFrame(
        {
            "AAA": [1000] * len(dates),
            "BBB": [2000] * len(dates),
            "CCC": [500] * len(dates),
            "DDD": [800] * len(dates),
        },
        index=dates,
    )
    book_value = pd.DataFrame(
        {
            "AAA": [500] * len(dates),
            "BBB": [900] * len(dates),
            "CCC": [400] * len(dates),
            "DDD": [300] * len(dates),
        },
        index=dates,
    )
    sector = pd.DataFrame(
        {
            "AAA": ["tech"] * len(dates),
            "BBB": ["tech"] * len(dates),
            "CCC": ["health"] * len(dates),
            "DDD": ["health"] * len(dates),
        },
        index=dates,
    )
    known_at = pd.DataFrame({col: dates for col in close.columns}, index=dates)
    return {
        "close": close,
        "market_cap": market_cap,
        "book_value": book_value,
        "sector": sector,
        "known_at": known_at,
    }


def _pipeline():
    return {
        "schema_version": "factor_pipeline.v1",
        "data_requirements": {
            "price_fields": ["close"],
            "fundamental_fields": ["book_value", "market_cap"],
            "classification_fields": ["sector"],
            "point_in_time_required": True,
        },
        "construction": [
            {
                "name": "momentum_2",
                "family": "momentum",
                "op": "factor.price_momentum",
                "inputs": {"close": "close", "lookback": 2},
                "known_at": "known_at",
            },
            {
                "name": "book_to_market",
                "family": "value",
                "op": "factor.book_to_market",
                "inputs": {"book_value": "book_value", "market_cap": "market_cap"},
                "known_at": "known_at",
            },
        ],
        "preprocessing": [
            {"op": "winsorize", "limits": [0.0, 1.0]},
            {"op": "standardize"},
            {"op": "neutralize", "group_by": ["sector"]},
        ],
        "composite": {
            "method": "equal_weight",
            "inputs": ["momentum_2", "book_to_market"],
            "output": "composite_factor_score",
        },
        "point_in_time": {"known_at_field": "known_at", "fail_on_lookahead": True},
        "cache": {"enabled": False, "storage": "local_parquet"},
        "outputs": {"factor_score_frame": True},
    }


def test_factorhandler_materializes_factor_clean_and_score_frames():
    from factorhandler import FactorHandler

    result = FactorHandler(_factor_frames(), _pipeline()).run()

    assert set(result.factor_frame) == {"momentum_2", "book_to_market"}
    assert set(result.clean_factor_frame) == {"momentum_2", "book_to_market"}
    assert set(result.factor_score_frame) == {"composite_factor_score"}
    score = result.factor_score_frame["composite_factor_score"]
    assert list(score.columns) == ["AAA", "BBB", "CCC", "DDD"]
    assert result.point_in_time_audit["status"] == "passed"
    assert result.factor_quality_report["missing_fields"] == []


def test_factorhandler_neutralize_group_means_are_near_zero():
    from factorhandler import FactorHandler

    result = FactorHandler(_factor_frames(), _pipeline()).run()
    clean = result.clean_factor_frame["book_to_market"].dropna()
    sector = _factor_frames()["sector"].reindex(index=clean.index)

    for date in clean.index:
        for group in ["tech", "health"]:
            members = sector.loc[date][sector.loc[date] == group].index
            assert clean.loc[date, members].mean() == pytest.approx(0.0, abs=1e-12)


def test_factorhandler_rejects_point_in_time_lookahead():
    from factorhandler import FactorHandler, FactorHandlerError

    frames = _factor_frames()
    frames["known_at"] = frames["known_at"].copy()
    frames["known_at"].iloc[2, 0] = pd.Timestamp("2024-02-01")

    with pytest.raises(FactorHandlerError, match="point-in-time lookahead detected"):
        FactorHandler(frames, _pipeline()).run()


def test_factorhandler_cache_key_changes_with_pipeline_config():
    from factorhandler import FactorHandler

    frames = _factor_frames()
    first = FactorHandler.cache_key(frames, _pipeline())
    modified = _pipeline()
    modified["composite"]["method"] = "manual_weight"
    modified["composite"]["weights"] = {"momentum_2": 1.0, "book_to_market": 0.0}
    second = FactorHandler.cache_key(frames, modified)

    assert first != second


def test_factorhandler_local_parquet_cache_roundtrip(tmp_path):
    from factorhandler import FactorHandler

    pipeline = _pipeline()
    pipeline["cache"] = {"enabled": True, "storage": "local_parquet"}

    first = FactorHandler(_factor_frames(), pipeline, cache_dir=tmp_path).run()
    second = FactorHandler(_factor_frames(), pipeline, cache_dir=tmp_path).run()

    assert first.cache_report["writes"] == 1
    assert second.cache_report["hits"] == 1
    pd.testing.assert_frame_equal(
        first.factor_score_frame["composite_factor_score"],
        second.factor_score_frame["composite_factor_score"],
        check_freq=False,
    )


def test_multi_asset_engine_can_rank_by_factorhandler_score():
    from backtester.MultiAssetPortfolioEngine_backtester import MultiAssetPortfolioEngineBacktester

    config = {
        "strategy_id": "factor_score_top1",
        "universe": {"symbols": ["AAA", "BBB", "CCC", "DDD"]},
        "factor_pipeline": _pipeline(),
        "computed_fields": [],
        "rebalance": {"trigger": {"op": "calendar.every_session"}},
        "selection": {
            "eligible": {"field": "composite_factor_score", "op": "gt", "value": -10},
            "rank_by": "composite_factor_score",
            "rank_order": "desc",
            "top_n": 1,
        },
        "allocation": {"method": "equal_weight", "position_limit": 1.0},
        "fill_model": {"cost": {"transaction_cost": 0.0, "slippage": 0.0}},
    }

    result = MultiAssetPortfolioEngineBacktester(_factor_frames(), config).run()

    assert result.feature_cache["factorhandler_frames"] >= 3
    assert result.validation_report["factorhandler"]["point_in_time_audit"]["status"] == "passed"
    selected = result.holdings[result.holdings["Selected"]]
    assert not selected.empty
    assert set(selected["Asset"]).issubset({"AAA", "BBB", "CCC", "DDD"})


def test_factorhandler_exports_artifacts_and_statanalyser_consumes_scores(tmp_path):
    from factorhandler import FactorArtifactExporter, FactorHandler
    from statanalyser.FactorArtifactAnalyser_statanalyser import FactorArtifactAnalyserStatanalyser

    result = FactorHandler(_factor_frames(), _pipeline()).run()
    paths = FactorArtifactExporter(result, tmp_path, run_id="factor_probe").export()

    assert any(path.endswith("_factor-score-frame_composite_factor_score.parquet") for path in paths)
    assert any(path.endswith("_factorhandler-reports.json") for path in paths)

    returns = _factor_frames()["close"].pct_change()
    analysis = FactorArtifactAnalyserStatanalyser(result.factor_score_frame, returns).run()

    assert "composite_factor_score" in analysis.ic_summary["factors"]
    assert analysis.coverage_summary["factors"]["composite_factor_score"]["coverage"] > 0.0
