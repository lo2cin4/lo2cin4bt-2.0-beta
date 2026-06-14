from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from wfanalyser.UnifiedPortfolioWFARunner_wfanalyser import UnifiedPortfolioWFARunner

pytestmark = pytest.mark.regression


def _runner_with_wfa_config(wfa_config: dict) -> UnifiedPortfolioWFARunner:
    runner = UnifiedPortfolioWFARunner.__new__(UnifiedPortfolioWFARunner)
    runner.wfa_config = wfa_config
    return runner


def test_wfa_helper_json_safe_and_risk_gate_summary_are_public_payload_safe() -> None:
    summary = UnifiedPortfolioWFARunner._risk_gate_summary(
        SimpleNamespace(
            validation_report={
                "risk_gate_summary": {
                    "event_count": np.int64(2),
                    "last_event_time": pd.Timestamp("2024-01-03 09:30:00"),
                    "bad_ratio": np.inf,
                    "gates_triggered": ("drawdown", "exposure"),
                }
            }
        )
    )

    assert summary == {
        "event_count": 2,
        "last_event_time": "2024-01-03T09:30:00",
        "bad_ratio": None,
        "gates_triggered": ["drawdown", "exposure"],
    }


def test_wfa_helper_acceptance_rejects_weak_oos_and_unapproved_fallback() -> None:
    runner = _runner_with_wfa_config(
        {
            "acceptance": {
                "min_oos_sharpe": 0.5,
                "min_oos_is_ratio": 0.75,
                "allow_selection_constraints_fallback_acceptance": False,
            }
        }
    )

    rejected = runner._acceptance(
        objective="sharpe",
        train_metrics={"sharpe": 2.0},
        oos_metrics={"sharpe": 1.0},
        selection_constraints_fallback=True,
    )

    assert rejected["accepted"] is False
    assert rejected["review_status"] == "Review"
    assert "OOS/IS ratio < 0.75" in rejected["reasons"]
    assert "selection_constraints_fallback requires explicit acceptance opt-in" in rejected["reasons"]


def test_wfa_helper_acceptance_allows_explicit_fallback_when_oos_is_strong() -> None:
    runner = _runner_with_wfa_config(
        {
            "acceptance": {
                "min_oos_calmar": 0.5,
                "min_oos_is_ratio": 0.5,
                "allow_selection_constraints_fallback_acceptance": "true",
            }
        }
    )

    accepted = runner._acceptance(
        objective="calmar",
        train_metrics={"calmar": 1.0},
        oos_metrics={"calmar": 0.8},
        selection_constraints_fallback=True,
    )

    assert accepted["accepted"] is True
    assert accepted["review_status"] == "Pass"
    assert accepted["oos_is_ratio"] == pytest.approx(0.8)
    assert accepted["reasons"] == ["selection_constraints_fallback explicitly allowed"]


def test_wfa_helper_strategy_max_lookback_scans_domains_computed_fields_and_params() -> None:
    config = {
        "parameter_domains": {
            "fast_window": {"type": "range", "start": 5, "end": 15, "step": 5},
            "slow_period": [20, 40],
        },
        "computed_fields": [
            {"name": "fast_ma", "period": {"param_ref": "fast_window"}},
            {"name": "slow_ma", "lookback": {"param_ref": "slow_period"}},
            {"name": "literal_ma", "sma_period": 30},
        ],
    }

    assert UnifiedPortfolioWFARunner._strategy_max_lookback_days(config) == 40
    assert UnifiedPortfolioWFARunner._strategy_max_lookback_days(
        config,
        params={"slow_period": 25, "extra_window": 60},
    ) == 60


def test_wfa_helper_window_backtest_id_and_slug_are_stable() -> None:
    assert UnifiedPortfolioWFARunner._window_backtest_id(
        window_id=7,
        objective="total return",
        params={"fast/window": 10, "risk mode": "low vol"},
    ) == "wfa_window_007_total-return_fast-window_10_risk-mode_low-vol"


def test_wfa_helper_exposure_and_return_activity_handle_empty_and_weight_frames() -> None:
    empty = pd.DataFrame()
    assert UnifiedPortfolioWFARunner._exposure_ratio(empty) == 0.0
    assert UnifiedPortfolioWFARunner._nonzero_return_days(empty) == 0

    equity = pd.DataFrame(
        {
            "Equity_value": [100.0, 101.0, 101.0, 99.0],
            "Weight_AAA": [0.0, 0.6, 0.6, 0.0],
            "Weight_BBB": [0.0, 0.4, 0.0, 0.0],
        }
    )

    assert UnifiedPortfolioWFARunner._exposure_ratio(equity) == pytest.approx(0.5)
    assert UnifiedPortfolioWFARunner._nonzero_return_days(equity) == 2
