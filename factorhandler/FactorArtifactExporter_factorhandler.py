"""Export FactorHandler artifacts to stable parquet/json files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .FactorHandler_factorhandler import FactorHandlerResult


class FactorArtifactExporter:
    def __init__(self, result: FactorHandlerResult, output_dir: Path | str, *, run_id: str) -> None:
        self.result = result
        self.output_dir = Path(output_dir)
        self.run_id = str(run_id)

    def export(self) -> List[str]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        paths: List[str] = []
        paths.extend(self._export_frame_group("factor-frame", self.result.factor_frame))
        paths.extend(self._export_frame_group("clean-factor-frame", self.result.clean_factor_frame))
        paths.extend(self._export_frame_group("factor-score-frame", self.result.factor_score_frame))
        reports = {
            "schema_version": "factorhandler_artifact_reports.v1",
            "factor_quality_report": self.result.factor_quality_report,
            "point_in_time_audit": self.result.point_in_time_audit,
            "cache_report": self.result.cache_report,
        }
        report_path = self.output_dir / f"{self.run_id}_factorhandler-reports.json"
        report_path.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
        paths.append(str(report_path))
        return paths

    def _export_frame_group(self, artifact_type: str, frames: Dict[str, pd.DataFrame]) -> List[str]:
        paths: List[str] = []
        for name, frame in frames.items():
            path = self.output_dir / f"{self.run_id}_{artifact_type}_{self._safe_name(name)}.parquet"
            frame.to_parquet(path, index=True, compression="zstd")
            paths.append(str(path))
        return paths

    @staticmethod
    def _safe_name(value: Any) -> str:
        text = str(value)
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)
        return safe or "factor"
