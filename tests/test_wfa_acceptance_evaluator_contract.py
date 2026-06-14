import pytest

from wfanalyser.WFAAcceptanceEvaluator_wfanalyser import WFAAcceptanceEvaluator

pytestmark = pytest.mark.regression


def test_wfa_acceptance_normalizes_invalid_metrics_without_score() -> None:
    result = WFAAcceptanceEvaluator().evaluate(
        {
            "mean_is_sharpe": "not-a-number",
            "mean_oos_sharpe": "also-bad",
            "oos_is_ratio": "bad",
        }
    )

    assert result.accepted is False
    assert result.robust_score is None
    assert result.metrics["mean_is_sharpe"] is None
    assert result.metrics["mean_oos_sharpe"] is None
    assert "oos_sharpe_not_positive" in result.reasons


def test_wfa_acceptance_rejects_low_oos_is_ratio() -> None:
    result = WFAAcceptanceEvaluator({"min_oos_is_ratio": 0.7}).evaluate(
        {"mean_is_sharpe": 2.0, "mean_oos_sharpe": 1.0}
    )

    assert result.accepted is False
    assert result.metrics["oos_is_ratio"] == pytest.approx(0.5)
    assert "oos_is_ratio_below_threshold" in result.reasons


def test_wfa_acceptance_ratio_gate_does_not_apply_to_nonpositive_is_or_oos() -> None:
    result = WFAAcceptanceEvaluator({"min_oos_is_ratio": 0.7, "min_oos_sharpe": -1.0}).evaluate(
        {"mean_is_sharpe": -2.0, "mean_oos_sharpe": 0.5, "oos_is_ratio": 0.1}
    )

    assert result.accepted is True
    assert "oos_is_ratio_below_threshold" not in result.reasons
    assert result.robust_score == pytest.approx(0.5)


def test_wfa_acceptance_applies_optional_risk_and_trade_quality_gates() -> None:
    result = WFAAcceptanceEvaluator(
        {
            "min_oos_sharpe": 0.0,
            "min_oos_calmar": 0.5,
            "max_drawdown_floor": -0.2,
            "min_profit_factor": 1.2,
            "min_win_rate": 0.55,
            "min_trade_count": 20,
        }
    ).evaluate(
        {
            "mean_is_sharpe": 1.0,
            "mean_oos_sharpe": 0.8,
            "mean_oos_calmar": 0.4,
            "max_drawdown": -0.35,
            "profit_factor": 1.1,
            "win_rate": 0.5,
            "trade_count": 10,
        }
    )

    assert result.accepted is False
    assert set(result.reasons) == {
        "oos_calmar_not_positive",
        "max_drawdown_floor_breached",
        "profit_factor_below_threshold",
        "win_rate_below_threshold",
        "trade_count_below_threshold",
    }


def test_wfa_acceptance_robust_score_subtracts_stability_and_drawdown_penalties() -> None:
    result = WFAAcceptanceEvaluator(
        {
            "min_oos_is_ratio": 0.5,
            "stability_penalty_weight": 0.5,
            "drawdown_penalty_weight": 0.25,
        }
    ).evaluate(
        {
            "mean_is_sharpe": 2.0,
            "mean_oos_sharpe": 1.0,
            "oos_std": 0.2,
            "max_drawdown": -0.4,
        }
    )

    assert result.accepted is True
    assert result.robust_score == pytest.approx(1.0 + 0.5 - 0.1 - 0.1)
