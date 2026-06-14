"""StatAnalyser helpers for already-materialized factor artifacts.

This analyser consumes factorhandler output frames.  It intentionally does not
construct, clean, or neutralize factors again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

import numpy as np
import pandas as pd


@dataclass
class FactorArtifactAnalysis:
    ic_summary: Dict[str, Any]
    coverage_summary: Dict[str, Any]


class FactorArtifactAnalyserStatanalyser:
    def __init__(
        self,
        factor_score_frame: Mapping[str, pd.DataFrame],
        returns_frame: pd.DataFrame,
    ) -> None:
        self.factor_score_frame = {str(key): self._normalize_frame(value) for key, value in factor_score_frame.items()}
        self.returns_frame = self._normalize_frame(returns_frame)

    def run(self) -> FactorArtifactAnalysis:
        return FactorArtifactAnalysis(
            ic_summary=self._ic_summary(),
            coverage_summary=self._coverage_summary(),
        )

    def _ic_summary(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"schema_version": "factor_ic_summary.v1", "factors": {}}
        forward_returns = self.returns_frame.shift(-1)
        for name, score in self.factor_score_frame.items():
            aligned_score = score.reindex(index=forward_returns.index, columns=forward_returns.columns)
            daily_ic = []
            daily_rank_ic = []
            for date in aligned_score.index:
                joined = pd.DataFrame(
                    {
                        "score": aligned_score.loc[date],
                        "forward_return": forward_returns.loc[date],
                    }
                ).replace([np.inf, -np.inf], np.nan).dropna()
                if len(joined) < 2:
                    continue
                daily_ic.append(float(joined["score"].corr(joined["forward_return"], method="pearson")))
                daily_rank_ic.append(float(joined["score"].corr(joined["forward_return"], method="spearman")))
            summary["factors"][name] = {
                "observations": len(daily_ic),
                "mean_ic": self._mean(daily_ic),
                "mean_rank_ic": self._mean(daily_rank_ic),
            }
        return summary

    def _coverage_summary(self) -> Dict[str, Any]:
        return {
            "schema_version": "factor_coverage_summary.v1",
            "factors": {
                name: {
                    "rows": int(len(frame)),
                    "assets": int(len(frame.columns)),
                    "coverage": float(frame.notna().mean().mean()) if len(frame) and len(frame.columns) else 0.0,
                    "missingness": float(frame.isna().mean().mean()) if len(frame) and len(frame.columns) else 1.0,
                }
                for name, frame in self.factor_score_frame.items()
            },
        }

    @staticmethod
    def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
        out = out.sort_index()
        out.columns = [str(col).upper() for col in out.columns]
        return out

    @staticmethod
    def _mean(values: list[float]) -> float | None:
        cleaned = [value for value in values if pd.notna(value)]
        return float(np.mean(cleaned)) if cleaned else None
