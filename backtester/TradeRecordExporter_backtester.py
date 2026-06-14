"""Export backtest trade records to CSV, Excel, and Parquet."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rich.table import Table

from utils import show_error, show_info, show_success, show_warning

from .utils.ConsoleUtils_utils_backtester import get_console

console = get_console()

_AUDIT_JSON_INLINE_ROW_LIMIT = 1000
_AUDIT_JSON_CHUNK_SIZE = 2000
_AUDIT_PARQUET_COMPRESSION = "zstd"


class TradeRecordExporter_backtester:
    def __init__(
        self,
        trade_records: pd.DataFrame,
        frequency: str,
        trade_params: Optional[dict] = None,
        predictor: Optional[str] = None,
        Backtest_id: str = "",
        results: Optional[List[dict]] = None,
        transaction_cost: Optional[float] = None,
        slippage: Optional[float] = None,
        trade_delay: Optional[int] = None,
        trade_price: Optional[str] = None,
        data: Optional[pd.DataFrame] = None,
        predictor_file_name: Optional[str] = None,
        predictor_column: Optional[str] = None,
        symbol: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> None:
        self.trade_records = trade_records if isinstance(trade_records, pd.DataFrame) else pd.DataFrame()
        self.frequency = frequency
        self.trade_params = trade_params or {}
        self.predictor = predictor
        self.Backtest_id = Backtest_id
        self.results = results or []
        self.transaction_cost = transaction_cost
        self.slippage = slippage
        self.trade_delay = trade_delay
        self.trade_price = trade_price
        self.data = data if isinstance(data, pd.DataFrame) else pd.DataFrame()
        self.predictor_file_name = predictor_file_name
        self.predictor_column = predictor_column
        self.symbol = symbol
        self.logger = logging.getLogger(self.__class__.__name__)
        self.last_exported_path: Optional[str] = None

        if output_dir:
            self.output_dir = os.fspath(output_dir)
        else:
            self.output_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "outputs",
                "backtester",
            )
        os.makedirs(self.output_dir, exist_ok=True)

    def _get_strategy_name(self, params: Optional[dict]) -> str:
        if not isinstance(params, dict):
            return "Unknown"

        if str(params.get("strategy_mode", "")).lower() == "semantic":
            semantic_label = str(params.get("semantic_run_label", "")).strip()
            if semantic_label:
                return semantic_label
            semantic_fields = params.get("semantic_fields", [])
            if isinstance(semantic_fields, list) and semantic_fields:
                return "semantic_" + "_".join(self._field_display_slug(field) for field in semantic_fields)
            return "semantic"

        def param_to_str(param: Any) -> str:
            if isinstance(param, dict):
                return str(param.get("indicator_type") or "unknown")
            indicator_type = getattr(param, "indicator_type", None)
            if indicator_type:
                return str(indicator_type)
            return str(param)

        entry = "+".join(param_to_str(item) for item in params.get("entry", []))
        exit_ = "+".join(param_to_str(item) for item in params.get("exit", []))
        return f"{entry}_{exit_}" if entry or exit_ else "Unknown"

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip())
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug or "unknown"

    @staticmethod
    def _field_display_slug(field_name: str) -> str:
        raw = str(field_name).strip()
        if not raw:
            return "field"
        tokens = [token for token in raw.split(".") if token]
        if not tokens:
            return TradeRecordExporter_backtester._slugify(raw)
        if len(tokens) >= 2 and tokens[0].lower() == "feature":
            return TradeRecordExporter_backtester._slugify(tokens[-2])
        return TradeRecordExporter_backtester._slugify(tokens[-1])

    def _get_result_trading_instrument(self, result: Dict[str, Any]) -> str:
        records = result.get("records", pd.DataFrame())
        if isinstance(records, pd.DataFrame) and not records.empty and "Trading_instrument" in records.columns:
            instrument = records["Trading_instrument"].iloc[0]
            if instrument and str(instrument) not in {"nan", "X"}:
                return str(instrument)
        return self._get_trading_instrument()

    def _build_export_filename(self, *, result: Dict[str, Any], extension: str) -> str:
        date_str = datetime.now().strftime("%Y%m%d")
        backtest_id = str(result.get("Backtest_id", self.Backtest_id or "unknown"))
        params = result.get("params", {}) if isinstance(result, dict) else {}
        instrument = self._slugify(self._get_result_trading_instrument(result))
        if isinstance(params, dict) and str(params.get("strategy_mode", "")).lower() == "semantic":
            semantic_label = self._slugify(str(params.get("semantic_run_label") or self._get_strategy_name(params)))
            return f"{date_str}_{self.frequency}_semantic_{instrument}_{semantic_label}_{backtest_id[:8]}.{extension}"

        predictor = self._slugify(str(params.get("predictor", "unknown")))
        strategy = self._slugify(self._get_strategy_name(params))
        return f"{date_str}_{self.frequency}_{strategy}_{predictor}_{backtest_id[:8]}.{extension}"

    def _get_results_to_export(self, backtest_id: Optional[str] = None) -> List[dict]:
        if backtest_id:
            return [result for result in self.results if result.get("Backtest_id") == backtest_id]
        return list(self.results)

    @staticmethod
    def _audit_suffix(field_name: str, suffix: str) -> str:
        safe = "".join(ch if ch.isalnum() else "_" for ch in str(field_name).strip().lower())
        while "__" in safe:
            safe = safe.replace("__", "_")
        safe = safe.strip("_") or "field"
        return f"__audit__{safe}__{suffix}"

    @staticmethod
    def _sidecar_base_path(primary_filepath: str) -> Path:
        path = Path(primary_filepath)
        return path.with_suffix("")

    @staticmethod
    def _index_audit_fields(params: Dict[str, Any]) -> Dict[str, Any]:
        keys = (
            "execution_plan_hash",
            "feature_contract_path",
            "feature_contract_hash",
            "source_audit_id",
            "strategy_contract_path",
            "strategy_mode",
        )
        return {
            key: params.get(key)
            for key in keys
            if isinstance(params, dict) and params.get(key) not in (None, "", [])
        }

    def _prepare_records_for_export(self, result: Dict[str, Any]) -> pd.DataFrame:
        records = result.get("records")
        if not isinstance(records, pd.DataFrame) or records.empty:
            return pd.DataFrame()
        out = records
        mutated = False
        if "Backtest_id" not in out.columns:
            out = out.copy()
            mutated = True
            out["Backtest_id"] = result.get("Backtest_id", "")
        params = result.get("params", {}) if isinstance(result, dict) else {}
        for key, value in self._index_audit_fields(params).items():
            if key not in out.columns:
                if not mutated:
                    out = out.copy()
                    mutated = True
                out[key] = value
        return out

    def _extract_feature_audit_frame(self, results_to_export: List[dict]) -> pd.DataFrame:
        long_rows: List[Dict[str, Any]] = []
        if not isinstance(self.data, pd.DataFrame) or self.data.empty:
            return pd.DataFrame()
        for result in results_to_export:
            params = result.get("params", {}) if isinstance(result, dict) else {}
            semantic_fields = params.get("semantic_fields", []) if isinstance(params, dict) else []
            records = result.get("records")
            if not isinstance(semantic_fields, list) or not semantic_fields:
                continue
            if not isinstance(records, pd.DataFrame) or records.empty:
                continue
            row_count = min(len(records), len(self.data))
            if row_count <= 0:
                continue
            records_slice = records.iloc[:row_count].reset_index(drop=True)
            data_slice = self.data.iloc[:row_count].reset_index(drop=True)
            for field_name in semantic_fields:
                feature_col = str(field_name)
                source_id_col = self._audit_suffix(feature_col, "source_id")
                if source_id_col not in data_slice.columns:
                    continue
                source_uri_col = self._audit_suffix(feature_col, "source_uri")
                source_time_col = self._audit_suffix(feature_col, "source_time")
                join_mode_col = self._audit_suffix(feature_col, "join_mode")
                value_origin_col = self._audit_suffix(feature_col, "value_origin")
                age_bars_col = self._audit_suffix(feature_col, "age_bars")
                was_filled_col = self._audit_suffix(feature_col, "was_filled")
                lag_applied_col = self._audit_suffix(feature_col, "lag_applied")
                stale_flag_col = self._audit_suffix(feature_col, "stale_flag")
                for idx in range(row_count):
                    long_rows.append(
                        {
                            "Backtest_id": result.get("Backtest_id", ""),
                            "Time": records_slice.loc[idx, "Time"] if "Time" in records_slice.columns else pd.NaT,
                            "feature_field": feature_col,
                            "feature_value": data_slice.loc[idx, feature_col] if feature_col in data_slice.columns else pd.NA,
                            "source_id": data_slice.loc[idx, source_id_col],
                            "source_uri": data_slice.loc[idx, source_uri_col] if source_uri_col in data_slice.columns else "",
                            "source_time": data_slice.loc[idx, source_time_col] if source_time_col in data_slice.columns else pd.NaT,
                            "join_mode": data_slice.loc[idx, join_mode_col] if join_mode_col in data_slice.columns else "",
                            "value_origin": data_slice.loc[idx, value_origin_col] if value_origin_col in data_slice.columns else "",
                            "age_bars": data_slice.loc[idx, age_bars_col] if age_bars_col in data_slice.columns else pd.NA,
                            "was_filled": bool(data_slice.loc[idx, was_filled_col]) if was_filled_col in data_slice.columns else False,
                            "lag_applied": data_slice.loc[idx, lag_applied_col] if lag_applied_col in data_slice.columns else 0,
                            "stale_flag": bool(data_slice.loc[idx, stale_flag_col]) if stale_flag_col in data_slice.columns else False,
                            **self._index_audit_fields(params),
                        }
                    )
        if not long_rows:
            return pd.DataFrame()
        return pd.DataFrame(long_rows)

    def _build_audit_sidecar_payload(
        self,
        *,
        primary_artifact: str,
        primary_filepath: str,
        results_to_export: List[dict],
        audit_frame: pd.DataFrame,
    ) -> Dict[str, Any]:
        summary_rows: List[Dict[str, Any]] = []
        if not audit_frame.empty:
            grouped = (
                audit_frame.groupby(["feature_field", "source_id", "source_uri", "join_mode"], dropna=False)
                .agg(
                    rows=("feature_field", "size"),
                    stale_rows=("stale_flag", "sum"),
                    filled_rows=("was_filled", "sum"),
                    max_age_bars=("age_bars", "max"),
                )
                .reset_index()
            )
            summary_rows = grouped.to_dict(orient="records")

        result_ids = [str(item.get("Backtest_id", "")) for item in results_to_export if isinstance(item, dict)]
        first_params = next(
            (
                item.get("params", {})
                for item in results_to_export
                if isinstance(item, dict) and isinstance(item.get("params"), dict)
            ),
            {},
        )
        return {
            "schema_version": "1.0",
            "artifact_type": primary_artifact,
            "primary_artifact_path": str(primary_filepath),
            "generated_at": datetime.now().isoformat(),
            "backtest_ids": result_ids,
            "summary_index": self._index_audit_fields(first_params if isinstance(first_params, dict) else {}),
            "source_audit_id": str(first_params.get("source_audit_id", "")) if isinstance(first_params, dict) else "",
            "feature_contract_path": str(first_params.get("feature_contract_path", "")) if isinstance(first_params, dict) else "",
            "feature_contract_hash": str(first_params.get("feature_contract_hash", "")) if isinstance(first_params, dict) else "",
            "execution_plan_hash": str(first_params.get("execution_plan_hash", "")) if isinstance(first_params, dict) else "",
            "semantic_fields": list(first_params.get("semantic_fields", [])) if isinstance(first_params, dict) else [],
            "feature_audit_summary": summary_rows,
            "audit_row_count": int(len(audit_frame)),
            "audit_parquet_compression": _AUDIT_PARQUET_COMPRESSION,
            "audit_json_inline_row_limit": _AUDIT_JSON_INLINE_ROW_LIMIT,
        }

    @staticmethod
    def _chunk_records(records: List[Dict[str, Any]], chunk_size: int) -> List[List[Dict[str, Any]]]:
        if chunk_size <= 0:
            return [records]
        return [records[i : i + chunk_size] for i in range(0, len(records), chunk_size)]

    def _write_audit_json_chunks(
        self,
        *,
        sidecar_base: Path,
        audit_rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        chunks = self._chunk_records(audit_rows, _AUDIT_JSON_CHUNK_SIZE)
        manifests: List[Dict[str, Any]] = []
        for idx, chunk_rows in enumerate(chunks, start=1):
            chunk_name = f"{sidecar_base.name}_audit_rows_{idx:03d}.jsonl"
            chunk_path = sidecar_base.with_name(chunk_name)
            with chunk_path.open("w", encoding="utf-8") as handle:
                for row in chunk_rows:
                    handle.write(json.dumps(row, ensure_ascii=False, default=str))
                    handle.write("\n")
            manifests.append(
                {
                    "path": str(chunk_path),
                    "filename": chunk_name,
                    "row_count": len(chunk_rows),
                }
            )
        return manifests

    def _write_audit_sidecars(
        self,
        *,
        primary_filepath: str,
        primary_artifact: str,
        results_to_export: List[dict],
    ) -> None:
        audit_frame = self._extract_feature_audit_frame(results_to_export)
        if audit_frame.empty:
            return
        sidecar_base = self._sidecar_base_path(primary_filepath)
        payload = self._build_audit_sidecar_payload(
            primary_artifact=primary_artifact,
            primary_filepath=primary_filepath,
            results_to_export=results_to_export,
            audit_frame=audit_frame,
        )
        metadata_path = sidecar_base.with_name(sidecar_base.name + "_metadata.json")
        audit_json_path = sidecar_base.with_name(sidecar_base.name + "_audit.json")
        audit_parquet_path = sidecar_base.with_name(sidecar_base.name + "_audit.parquet")
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        audit_rows = audit_frame.to_dict(orient="records")
        audit_json_payload = dict(payload)
        if len(audit_rows) <= _AUDIT_JSON_INLINE_ROW_LIMIT:
            audit_json_payload["audit_rows_inline"] = True
            audit_json_payload["audit_rows"] = audit_rows
            audit_json_payload["audit_row_chunks"] = []
        else:
            audit_json_payload["audit_rows_inline"] = False
            audit_json_payload["audit_rows"] = []
            audit_json_payload["audit_row_chunks"] = self._write_audit_json_chunks(
                sidecar_base=sidecar_base,
                audit_rows=audit_rows,
            )
        audit_json_path.write_text(json.dumps(audit_json_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        audit_frame.to_parquet(audit_parquet_path, index=False, compression=_AUDIT_PARQUET_COMPRESSION)

    def _extract_calendar_signal_audit_frame(self, results_to_export: List[dict]) -> pd.DataFrame:
        frames: List[pd.DataFrame] = []
        for result in results_to_export:
            if not isinstance(result, dict):
                continue
            audit = result.get("calendar_signal_audit")
            if not isinstance(audit, pd.DataFrame) or audit.empty:
                continue
            frame = audit.copy()
            if "Backtest_id" not in frame.columns:
                frame["Backtest_id"] = result.get("Backtest_id", "")
            if "strategy_id" not in frame.columns:
                frame["strategy_id"] = result.get("strategy_id", "")
            if "combo" in frame.columns:
                frame["combo_json"] = frame["combo"].map(
                    lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                    if isinstance(value, dict)
                    else str(value or "")
                )
                frame = frame.drop(columns=["combo"])
            frames.append(frame)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True, sort=False)

    def _write_calendar_signal_audit(
        self,
        *,
        primary_filepath: str,
        results_to_export: List[dict],
    ) -> List[str]:
        audit_frame = self._extract_calendar_signal_audit_frame(results_to_export)
        if audit_frame.empty:
            return []
        sidecar_base = self._sidecar_base_path(primary_filepath)
        parquet_path = sidecar_base.with_name(sidecar_base.name + "_calendar_signal_audit.parquet")
        metadata_path = sidecar_base.with_name(sidecar_base.name + "_calendar_signal_audit_metadata.json")
        audit_frame.to_parquet(parquet_path, index=False, compression=_AUDIT_PARQUET_COMPRESSION)
        payload = {
            "schema_version": "1.0",
            "artifact_type": "calendar_signal_audit",
            "primary_artifact_path": str(primary_filepath),
            "generated_at": datetime.now().isoformat(),
            "backtest_ids": sorted(
                str(value)
                for value in audit_frame.get("Backtest_id", pd.Series(dtype=str)).dropna().unique().tolist()
            ),
            "row_count": int(len(audit_frame)),
            "triggered_count": int(audit_frame.get("triggered", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()),
            "skipped_count": int((~audit_frame.get("triggered", pd.Series(dtype=bool)).fillna(False).astype(bool)).sum()),
            "parquet_path": str(parquet_path),
            "parquet_compression": _AUDIT_PARQUET_COMPRESSION,
        }
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return [str(parquet_path), str(metadata_path)]

    def export_to_csv(self, backtest_id: Optional[str] = None) -> List[str]:
        if not self.results:
            show_warning("BACKTESTER", "No backtest results available for CSV export.")
            return []
        results_to_export = self._get_results_to_export(backtest_id)
        if backtest_id and not results_to_export:
            show_error("BACKTESTER", f"Backtest_id {backtest_id} not found.")
            return []

        exported_count = 0
        messages: List[str] = []
        exported_paths: List[str] = []
        for result in results_to_export:
            if result.get("error") is not None:
                messages.append(f"Skip failed result {result.get('Backtest_id')}: {result.get('error')}")
                continue
            records = result.get("records")
            if not isinstance(records, pd.DataFrame) or records.empty:
                messages.append(f"Skip empty result {result.get('Backtest_id')}")
                continue
            filename = self._build_export_filename(result=result, extension="csv")
            filepath = os.path.join(self.output_dir, filename)
            records_to_export = self._prepare_records_for_export(result)
            records_to_export.to_csv(filepath, index=False)
            self._write_audit_sidecars(
                primary_filepath=filepath,
                primary_artifact="backtest_csv",
                results_to_export=[result],
            )
            self.last_exported_path = filepath
            exported_count += 1
            exported_paths.append(filepath)
            messages.append(f"Exported CSV: {filename}")

        if exported_count == 0:
            show_error("BACKTESTER", "\n".join(messages or ["No CSV files were exported."]))
        else:
            show_success("BACKTESTER", "\n".join(messages + [f"CSV export completed: {exported_count} file(s). "]))
        return exported_paths

    def export_to_excel(self, backtest_id: Optional[str] = None) -> List[str]:
        if not self.results:
            show_warning("BACKTESTER", "No backtest results available for Excel export.")
            return []
        results_to_export = self._get_results_to_export(backtest_id)
        if backtest_id and not results_to_export:
            show_error("BACKTESTER", f"Backtest_id {backtest_id} not found.")
            return []

        exported_count = 0
        exported_paths: List[str] = []
        for result in results_to_export:
            if result.get("error") is not None:
                continue
            records = result.get("records")
            if not isinstance(records, pd.DataFrame) or records.empty:
                continue
            filename = self._build_export_filename(result=result, extension="xlsx")
            filepath = os.path.join(self.output_dir, filename)
            records_to_export = self._prepare_records_for_export(result)
            records_to_export.to_excel(filepath, index=False)
            self._write_audit_sidecars(
                primary_filepath=filepath,
                primary_artifact="backtest_excel",
                results_to_export=[result],
            )
            self.last_exported_path = filepath
            exported_count += 1
            exported_paths.append(filepath)

        if exported_count == 0:
            show_warning("BACKTESTER", "No Excel files were exported.")
        else:
            show_success("BACKTESTER", f"Excel export completed: {exported_count} file(s).")
        return exported_paths

    def _create_parquet_filename(self, results_to_export: Optional[List[dict]] = None) -> tuple[str, str]:
        date_str = datetime.now().strftime("%Y%m%d")
        selected = list(results_to_export or self.results or [])
        if len(selected) == 1:
            result = selected[0]
            params = result.get("params", {}) if isinstance(result, dict) else {}
            backtest_id = str(result.get("Backtest_id", self.Backtest_id or "batch"))
            instrument = self._slugify(self._get_result_trading_instrument(result))
            if isinstance(params, dict) and str(params.get("strategy_mode", "")).lower() == "semantic":
                semantic_label = self._slugify(
                    str(params.get("semantic_run_label") or self._get_strategy_name(params))
                )
                filename = (
                    f"{date_str}_{self.frequency}_semantic_{instrument}_{semantic_label}_{backtest_id[:8]}.parquet"
                )
            else:
                predictor = self._slugify(str(params.get("predictor", self.predictor or "unknown")))
                strategy = self._slugify(self._get_strategy_name(params))
                filename = f"{date_str}_{self.frequency}_{strategy}_{predictor}_{backtest_id[:8]}.parquet"
        else:
            batch_id = self._slugify(self.Backtest_id or uuid.uuid4().hex[:8])
            instrument = self._slugify(self._get_trading_instrument())
            filename = f"{date_str}_{self.frequency}_batch_{instrument}_{batch_id[:8]}.parquet"
        filepath = os.path.join(self.output_dir, filename)
        return filename, filepath

    def _get_trading_instrument(self) -> str:
        if self.symbol and self.symbol != "X":
            return str(self.symbol)
        for result in self.results:
            records = result.get("records")
            if isinstance(records, pd.DataFrame) and not records.empty and "Trading_instrument" in records.columns:
                instrument = records["Trading_instrument"].iloc[0]
                if instrument and str(instrument) not in {"nan", "X"}:
                    return str(instrument)
        if self.symbol:
            return str(self.symbol)
        return "X"

    def _filter_valid_records(self, results_to_export: List[dict]) -> List[pd.DataFrame]:
        valid: List[pd.DataFrame] = []
        for result in results_to_export:
            if result.get("error") is not None:
                continue
            records = self._prepare_records_for_export(result)
            if isinstance(records, pd.DataFrame) and not records.empty:
                valid.append(records)
        return valid

    @staticmethod
    def _concat_records_safely(records_list: List[pd.DataFrame]) -> pd.DataFrame:
        if not records_list:
            return pd.DataFrame()
        return pd.concat(records_list, ignore_index=True, sort=False, copy=False)

    @staticmethod
    def _dataframe_to_arrow_table(frame: pd.DataFrame) -> pa.Table:
        if frame.empty:
            return pa.table({})
        return pa.Table.from_pandas(frame, preserve_index=False)

    @staticmethod
    def _normalize_export_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        if "Trade_action" not in frame.columns:
            return frame
        trade_action = frame["Trade_action"]
        if pd.api.types.is_integer_dtype(trade_action.dtype):
            return frame
        normalized = frame.copy()
        normalized["Trade_action"] = pd.to_numeric(
            normalized["Trade_action"], errors="coerce"
        ).fillna(0).astype(int)
        return normalized

    def _combine_records(self, results_to_export: List[dict]) -> pd.DataFrame:
        valid_records = self._filter_valid_records(results_to_export)
        if len(valid_records) == 1:
            return self._normalize_export_frame(valid_records[0])
        combined = self._concat_records_safely(valid_records)
        return self._normalize_export_frame(combined)

    def export_to_parquet(self, backtest_id: Optional[str] = None) -> List[str]:
        if not self.results:
            show_warning("BACKTESTER", "No backtest results available for Parquet export.")
            return []
        results_to_export = self._get_results_to_export(backtest_id)
        if backtest_id and not results_to_export:
            show_error("BACKTESTER", f"Backtest_id {backtest_id} not found.")
            return []

        combined_records = self._combine_records(results_to_export)
        if combined_records.empty:
            show_warning("BACKTESTER", "No records available for Parquet export.")
            return []

        filename, filepath = self._create_parquet_filename(results_to_export)
        combined_table = self._dataframe_to_arrow_table(combined_records)
        pq.write_table(combined_table, filepath)
        self._write_audit_sidecars(
            primary_filepath=filepath,
            primary_artifact="backtest_parquet",
            results_to_export=results_to_export,
        )
        calendar_audit_paths = self._write_calendar_signal_audit(
            primary_filepath=filepath,
            results_to_export=results_to_export,
        )
        self.last_exported_path = filepath
        show_success("BACKTESTER", f"Parquet exported: {filename}")
        return [filepath, *calendar_audit_paths]

    def display_backtest_summary(self) -> None:
        if not self.results:
            show_info("BACKTESTER", "No backtest results available.")
            return
        if len(self.results) > 15:
            self._display_paginated_summary()
        else:
            self._display_full_summary()

    def _display_full_summary(self) -> None:
        table = Table(title="Backtest Summary", style="bold magenta")
        table.add_column("No.", style="cyan", no_wrap=True)
        table.add_column("Backtest ID", style="green", no_wrap=True)
        table.add_column("Strategy", style="blue")
        table.add_column("Status", style="yellow", no_wrap=True)
        for idx, result in enumerate(self.results, start=1):
            params = result.get("params")
            strategy = self._get_strategy_name(params) if params else "N/A"
            records = result.get("records")
            if result.get("error") is not None:
                status = "FAILED"
            elif isinstance(records, pd.DataFrame) and not records.empty and (records["Trade_action"] == 1).sum() > 0:
                status = "SUCCESS"
            else:
                status = "NO_TRADE"
            table.add_row(str(idx), str(result.get("Backtest_id", "N/A")), strategy, status)
        console.print(table)

    def _display_paginated_summary(self) -> None:
        self._display_full_summary()

    def _show_operation_menu(self) -> None:
        return

    def display_results_by_strategy(self) -> None:
        if not self.results:
            show_warning("BACKTESTER", "No backtest results available.")
            return
        strategy_groups: Dict[str, List[dict]] = {}
        for result in self.results:
            params = result.get("params")
            strategy = self._get_strategy_name(params) if params else "N/A"
            strategy_groups.setdefault(strategy, []).append(result)
        table = Table(title="Results By Strategy", style="bold magenta")
        table.add_column("No.", style="cyan", no_wrap=True)
        table.add_column("Strategy", style="green")
        table.add_column("Results", style="yellow", no_wrap=True)
        for idx, (strategy, grouped_results) in enumerate(strategy_groups.items(), start=1):
            table.add_row(str(idx), strategy, str(len(grouped_results)))
        console.print(table)

    def display_strategy_details(self, strategy: str, results: List[dict]) -> None:
        table = Table(title=f"Strategy Details: {strategy}", style="bold cyan")
        table.add_column("No.", style="cyan", no_wrap=True)
        table.add_column("Backtest ID", style="green", no_wrap=True)
        table.add_column("Status", style="yellow", no_wrap=True)
        for idx, result in enumerate(results, start=1):
            records = result.get("records")
            if result.get("error") is not None:
                status = "FAILED"
            elif isinstance(records, pd.DataFrame) and not records.empty and (records["Trade_action"] == 1).sum() > 0:
                status = "SUCCESS"
            else:
                status = "NO_TRADE"
            table.add_row(str(idx), str(result.get("Backtest_id", "N/A")), status)
        console.print(table)

    def display_successful_results(self) -> None:
        successful = []
        for result in self.results:
            records = result.get("records")
            if result.get("error") is None and isinstance(records, pd.DataFrame) and not records.empty and (records["Trade_action"] == 1).sum() > 0:
                successful.append(result)
        if not successful:
            show_info("BACKTESTER", "No successful results.")
            return
        self.display_strategy_details("successful", successful)

    def display_failed_results(self) -> None:
        failed = [result for result in self.results if result.get("error") is not None]
        if not failed:
            show_info("BACKTESTER", "No failed results.")
            return
        self.display_strategy_details("failed", failed)

    def debug_trade_actions(self) -> None:
        all_trade_actions: List[int] = []
        for result in self.results:
            records = result.get("records")
            if isinstance(records, pd.DataFrame) and not records.empty and "Trade_action" in records.columns:
                all_trade_actions.extend(records["Trade_action"].astype(int).tolist())
        if not all_trade_actions:
            show_warning("BACKTESTER", "No trade actions available.")
            return
        counts = pd.Series(all_trade_actions).value_counts().sort_index()
        table = Table(title="Trade Action Distribution", style="bold magenta")
        table.add_column("Trade Action", style="cyan", no_wrap=True)
        table.add_column("Count", style="green", no_wrap=True)
        for action, count in counts.items():
            table.add_row(str(action), str(int(count)))
        console.print(table)

    def display_no_trade_results(self) -> None:
        no_trade = []
        for result in self.results:
            records = result.get("records")
            if result.get("error") is None and (not isinstance(records, pd.DataFrame) or records.empty or (records["Trade_action"] == 1).sum() == 0):
                no_trade.append(result)
        if not no_trade:
            show_info("BACKTESTER", "No no-trade results.")
            return
        self.display_strategy_details("no_trade", no_trade)
