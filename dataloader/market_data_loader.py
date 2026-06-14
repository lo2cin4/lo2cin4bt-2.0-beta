"""Multi-asset market data provider entrypoint.

This module is the runtime boundary between provider-specific download logic
and the backtester. Backtester code should ask this loader for normalized
market frames instead of calling provider APIs directly.
"""

from __future__ import annotations

import threading
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.path_resolver import resolve_input_path


_YFINANCE_DOWNLOAD_LOCK = threading.Lock()


class MultiAssetMarketDataLoader:
    """Load normalized multi-asset market data frames from configured providers."""

    def __init__(self, *, repo_root: Path):
        self.repo_root = Path(repo_root)

    def load(
        self,
        spec: Any,
        *,
        config_file_path: Optional[str] = None,
    ) -> Dict[str, pd.DataFrame]:
        if not isinstance(spec, dict) or not spec:
            raise ValueError("backtester.market_data is required for multi_asset_portfolio")

        provider = str(spec.get("provider") or spec.get("source") or "").strip().lower()
        if provider in {"yfinance", "yf"}:
            return self._download_yfinance(spec)
        if provider in {"binance", "binance_spot"}:
            return self._download_binance(spec)
        if provider in {"coinbase", "coinbase_exchange"}:
            return self._download_coinbase(spec)
        if provider in {"futu", "futu_openapi"}:
            from dataloader.futu_loader import FutuMarketDataLoader

            return FutuMarketDataLoader().load_multi_asset(spec)
        if provider in {"ibkr", "interactive_brokers", "interactivebrokers"}:
            from dataloader.ibkr_loader import IBKRMarketDataLoader

            return IBKRMarketDataLoader().load_multi_asset(spec)

        return self._load_wide_frames(spec, config_file_path=config_file_path)

    def _load_wide_frames(
        self,
        spec: Dict[str, Any],
        *,
        config_file_path: Optional[str],
    ) -> Dict[str, pd.DataFrame]:
        frames: Dict[str, pd.DataFrame] = {}
        for field_name, field_spec in spec.items():
            key = str(field_name).strip().lower()
            if key in {"provider", "source", "symbols", "start", "start_date", "end", "end_date", "interval"}:
                continue
            if isinstance(field_spec, str):
                field_spec = {"path": field_spec}
            if not isinstance(field_spec, dict):
                raise ValueError(f"backtester.market_data.{field_name} must be a path or object")
            raw_path = str(field_spec.get("path") or "").strip()
            if not raw_path:
                raise ValueError(f"backtester.market_data.{field_name}.path is required")
            resolved = resolve_input_path(
                raw_path,
                repo_root=self.repo_root,
                config_file_path=config_file_path,
            )
            frames[key] = self._read_wide_market_frame(
                resolved.path,
                time_column=str(field_spec.get("time_column") or "Time"),
            )
        if not frames:
            raise ValueError("No file-backed market data fields were configured")
        return frames

    def _download_yfinance(self, spec: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
        try:
            import yfinance as yf
        except Exception as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("yfinance is required for provider=yfinance multi-asset data") from exc

        symbols = spec.get("symbols", [])
        if not isinstance(symbols, list) or not symbols:
            raise ValueError("backtester.market_data.symbols is required for yfinance multi-asset data")
        symbols = [str(item).strip().upper() for item in symbols if str(item).strip()]
        start = str(spec.get("start") or spec.get("start_date") or "1990-01-01")
        end = spec.get("end") or spec.get("end_date")
        interval = str(spec.get("interval") or "1d")
        timeout = int(spec.get("timeout") or spec.get("download_timeout") or 30)
        cache_identity = {
            "provider": "yfinance",
            "symbols": symbols,
            "start": start,
            "end": end,
            "interval": interval,
            "auto_adjust": True,
            "start_policy": spec.get("start_policy") or spec.get("dropna_policy") or "",
        }
        cache_path = self._market_cache_path(cache_identity)
        if self._market_cache_enabled(spec):
            cached_frames = self._read_market_cache(
                cache_path,
                max_age_seconds=self._market_cache_max_age_seconds(spec),
            )
            if cached_frames:
                return {key: frame.reindex(columns=symbols) for key, frame in cached_frames.items()}

        def download(tickers: List[str]) -> pd.DataFrame:
            return yf.download(
                tickers=tickers,
                start=start,
                end=end,
                interval=interval,
                auto_adjust=True,
                group_by="column",
                progress=False,
                threads=False,
                timeout=timeout,
            )

        def parse_raw(raw_frame: pd.DataFrame, requested_symbols: List[str]) -> Dict[str, pd.DataFrame]:
            frames_out: Dict[str, pd.DataFrame] = {}
            field_map = {
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
            for key, yf_field in field_map.items():
                if isinstance(raw_frame.columns, pd.MultiIndex):
                    if yf_field not in raw_frame.columns.get_level_values(0):
                        continue
                    frame = raw_frame[yf_field].copy()
                else:
                    if len(requested_symbols) != 1 or yf_field not in raw_frame.columns:
                        continue
                    frame = raw_frame[[yf_field]].copy()
                    frame.columns = requested_symbols
                frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
                frames_out[key] = frame.sort_index().apply(pd.to_numeric, errors="coerce")
            return frames_out

        # yfinance uses process-global state internally. Concurrent multi-asset
        # downloads can return partial/mixed frames, so serialize this call.
        with _YFINANCE_DOWNLOAD_LOCK:
            raw = download(symbols)
        if raw.empty:
            raw = self._download_yfinance_symbols_individually(download, symbols)
        frames = parse_raw(raw, symbols)
        if "close" not in frames:
            raise ValueError("yfinance multi-asset data did not include close prices")
        missing_symbols = [symbol for symbol in symbols if symbol not in frames["close"].columns]
        if missing_symbols:
            retry_raw = self._download_yfinance_symbols_individually(download, missing_symbols)
            retry_frames = parse_raw(retry_raw, missing_symbols)
            for key, retry_frame in retry_frames.items():
                if key in frames:
                    frames[key] = frames[key].join(retry_frame, how="outer")
                else:
                    frames[key] = retry_frame
            missing_symbols = [symbol for symbol in symbols if symbol not in frames["close"].columns]
            if missing_symbols:
                raise ValueError(
                    "yfinance multi-asset data missing requested symbols: "
                    + ", ".join(missing_symbols)
                )
        frames = {key: frame.reindex(columns=symbols) for key, frame in frames.items()}
        start_policy = str(
            spec.get("start_policy") or spec.get("dropna_policy") or ""
        ).strip().lower()
        if start_policy in {"common_available", "first_common", "all_symbols_available"}:
            common_dates = frames["close"].dropna(how="any").index
            if common_dates.empty:
                raise ValueError(
                    f"yfinance data has no common tradable date for symbols={symbols}"
                )
            first_common = pd.Timestamp(common_dates[0]).normalize()
            frames = {
                key: frame.loc[frame.index >= first_common].copy()
                for key, frame in frames.items()
            }
        if self._market_cache_enabled(spec):
            self._write_market_cache(cache_path, frames)
        return frames

    def _market_cache_path(self, identity: Dict[str, Any]) -> Path:
        raw = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return self.repo_root / "outputs" / "cache" / "market_data" / f"{digest}.parquet"

    @staticmethod
    def _market_cache_enabled(spec: Dict[str, Any]) -> bool:
        raw = spec.get("cache", spec.get("use_cache", True))
        if isinstance(raw, str):
            return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}
        return bool(raw)

    @staticmethod
    def _market_cache_max_age_seconds(spec: Dict[str, Any]) -> Optional[float]:
        raw_seconds = spec.get("cache_ttl_seconds")
        raw_hours = spec.get("cache_ttl_hours")
        try:
            if raw_seconds is not None:
                parsed = float(raw_seconds)
            elif raw_hours is not None:
                parsed = float(raw_hours) * 3600.0
            else:
                parsed = 12.0 * 3600.0
        except (TypeError, ValueError):
            parsed = 12.0 * 3600.0
        return None if parsed < 0 else max(0.0, parsed)

    @staticmethod
    def _read_market_cache(cache_path: Path, *, max_age_seconds: Optional[float]) -> Dict[str, pd.DataFrame]:
        if not cache_path.exists():
            return {}
        if max_age_seconds is not None:
            age = pd.Timestamp.utcnow().timestamp() - cache_path.stat().st_mtime
            if age > max_age_seconds:
                return {}
        try:
            frame = pd.read_parquet(cache_path)
        except Exception:
            return {}
        if not isinstance(frame.columns, pd.MultiIndex):
            return {}
        frames: Dict[str, pd.DataFrame] = {}
        for key in frame.columns.get_level_values(0).unique():
            frames[str(key)] = frame[key].copy()
        return frames

    @staticmethod
    def _write_market_cache(cache_path: Path, frames: Dict[str, pd.DataFrame]) -> None:
        if not frames:
            return
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            combined = pd.concat(frames, axis=1)
            combined.to_parquet(cache_path, compression="zstd")
        except Exception:
            return

    def _download_coinbase(self, spec: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
        try:
            import requests
        except Exception as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("requests is required for provider=coinbase multi-asset data") from exc

        symbols = spec.get("symbols", [])
        if not isinstance(symbols, list) or not symbols:
            raise ValueError("backtester.market_data.symbols is required for coinbase multi-asset data")
        symbols = [str(item).strip().upper() for item in symbols if str(item).strip()]
        start = str(spec.get("start") or spec.get("start_date") or "2017-01-01")
        end = spec.get("end") or spec.get("end_date")
        granularity = self._coinbase_granularity(str(spec.get("interval") or spec.get("frequency") or "1d"))
        api_base = str(spec.get("api_base") or "https://api.exchange.coinbase.com").rstrip("/")
        timeout = int(spec.get("timeout") or spec.get("download_timeout") or 30)

        field_frames: Dict[str, List[pd.Series]] = {
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
        }
        missing: List[str] = []
        for symbol in symbols:
            frame = self._download_coinbase_symbol(
                requests_module=requests,
                api_base=api_base,
                symbol=symbol,
                granularity=granularity,
                start=start,
                end=end,
                timeout=timeout,
            )
            if frame.empty:
                missing.append(symbol)
                continue
            for field in field_frames:
                field_frames[field].append(frame[field].rename(symbol))

        if missing:
            raise ValueError("coinbase data missing requested symbols: " + ", ".join(missing))
        if not field_frames["close"]:
            raise ValueError("coinbase returned no close prices")

        frames = {
            field: pd.concat(series_list, axis=1).sort_index()
            for field, series_list in field_frames.items()
        }
        frames = {key: frame.reindex(columns=symbols) for key, frame in frames.items()}
        start_policy = str(
            spec.get("start_policy") or spec.get("dropna_policy") or ""
        ).strip().lower()
        if start_policy in {"common_available", "first_common", "all_symbols_available"}:
            common_dates = frames["close"].dropna(how="any").index
            if common_dates.empty:
                raise ValueError(
                    f"coinbase data has no common tradable date for symbols={symbols}"
                )
            first_common = pd.Timestamp(common_dates[0]).normalize()
            frames = {
                key: frame.loc[frame.index >= first_common].copy()
                for key, frame in frames.items()
            }
        return frames

    @staticmethod
    def _download_coinbase_symbol(
        *,
        requests_module: Any,
        api_base: str,
        symbol: str,
        granularity: int,
        start: str,
        end: Any,
        timeout: int,
    ) -> pd.DataFrame:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end) if end else pd.Timestamp.utcnow().tz_localize(None)
        if end_ts <= start_ts:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        rows: List[List[Any]] = []
        url = f"{api_base}/products/{symbol}/candles"
        max_candles = 300
        batch_delta = timedelta(seconds=max_candles * int(granularity))
        current_start = start_ts.to_pydatetime()
        final_end = end_ts.to_pydatetime()

        while current_start < final_end:
            current_end = min(current_start + batch_delta, final_end)
            response = requests_module.get(
                url,
                params={
                    "start": current_start.isoformat(),
                    "end": current_end.isoformat(),
                    "granularity": int(granularity),
                },
                timeout=timeout,
            )
            response.raise_for_status()
            batch = response.json()
            if batch:
                rows.extend(batch)
            current_start = current_end

        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        frame = pd.DataFrame(
            rows,
            columns=["timestamp", "low", "high", "open", "close", "volume"],
        ).drop_duplicates(subset=["timestamp"])
        frame["Time"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True).dt.tz_localize(None).dt.normalize()
        for field in ["open", "high", "low", "close", "volume"]:
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
        return frame.set_index("Time")[["open", "high", "low", "close", "volume"]].sort_index()

    def _download_binance(self, spec: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
        try:
            import requests
        except Exception as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("requests is required for provider=binance multi-asset data") from exc

        symbols = spec.get("symbols", [])
        if not isinstance(symbols, list) or not symbols:
            raise ValueError("backtester.market_data.symbols is required for binance multi-asset data")
        symbols = [str(item).strip().upper().replace("/", "") for item in symbols if str(item).strip()]
        start = str(spec.get("start") or spec.get("start_date") or "2017-01-01")
        end = spec.get("end") or spec.get("end_date")
        interval = self._binance_interval(str(spec.get("interval") or spec.get("frequency") or "1d"))
        api_base = str(spec.get("api_base") or "https://api.binance.com").rstrip("/")
        timeout = int(spec.get("timeout") or spec.get("download_timeout") or 30)

        field_frames: Dict[str, List[pd.Series]] = {
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
        }
        missing: List[str] = []
        for symbol in symbols:
            frame = self._download_binance_symbol(
                requests_module=requests,
                api_base=api_base,
                symbol=symbol,
                interval=interval,
                start=start,
                end=end,
                timeout=timeout,
            )
            if frame.empty:
                missing.append(symbol)
                continue
            for field in field_frames:
                field_frames[field].append(frame[field].rename(symbol))

        if missing:
            raise ValueError("binance data missing requested symbols: " + ", ".join(missing))
        if not field_frames["close"]:
            raise ValueError("binance returned no close prices")

        frames = {
            field: pd.concat(series_list, axis=1).sort_index()
            for field, series_list in field_frames.items()
        }
        frames = {key: frame.reindex(columns=symbols) for key, frame in frames.items()}
        start_policy = str(
            spec.get("start_policy") or spec.get("dropna_policy") or ""
        ).strip().lower()
        if start_policy in {"common_available", "first_common", "all_symbols_available"}:
            common_dates = frames["close"].dropna(how="any").index
            if common_dates.empty:
                raise ValueError(
                    f"binance data has no common tradable date for symbols={symbols}"
                )
            first_common = pd.Timestamp(common_dates[0]).normalize()
            frames = {
                key: frame.loc[frame.index >= first_common].copy()
                for key, frame in frames.items()
            }
        return frames

    @staticmethod
    def _download_binance_symbol(
        *,
        requests_module: Any,
        api_base: str,
        symbol: str,
        interval: str,
        start: str,
        end: Any,
        timeout: int,
    ) -> pd.DataFrame:
        start_ms = int(pd.Timestamp(start).timestamp() * 1000)
        end_ms = int(pd.Timestamp(end).timestamp() * 1000) if end else None
        url = f"{api_base}/api/v3/klines"
        rows: List[List[Any]] = []
        while True:
            params: Dict[str, Any] = {
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms,
                "limit": 1000,
            }
            if end_ms is not None:
                params["endTime"] = end_ms
            response = requests_module.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            rows.extend(batch)
            next_start = int(batch[-1][0]) + 1
            if next_start <= start_ms:
                break
            start_ms = next_start
            if end_ms is not None and start_ms >= end_ms:
                break
            if len(batch) < 1000:
                break

        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        frame = pd.DataFrame(
            rows,
            columns=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_asset_volume",
                "number_of_trades",
                "taker_buy_base_asset_volume",
                "taker_buy_quote_asset_volume",
                "ignore",
            ],
        )
        frame["Time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
        for field in ["open", "high", "low", "close", "volume"]:
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
        return frame.set_index("Time")[["open", "high", "low", "close", "volume"]].sort_index()

    @staticmethod
    def _binance_interval(interval: str) -> str:
        normalized = interval.strip()
        aliases = {
            "1D": "1d",
            "1DAY": "1d",
            "D": "1d",
            "DAILY": "1d",
            "1H": "1h",
            "1M": "1m",
        }
        return aliases.get(normalized.upper(), normalized.lower())

    @staticmethod
    def _coinbase_granularity(interval: str) -> int:
        normalized = interval.strip().lower()
        aliases = {
            "1m": 60,
            "60": 60,
            "5m": 300,
            "300": 300,
            "15m": 900,
            "900": 900,
            "1h": 3600,
            "60m": 3600,
            "3600": 3600,
            "6h": 21600,
            "21600": 21600,
            "1d": 86400,
            "1day": 86400,
            "day": 86400,
            "daily": 86400,
            "86400": 86400,
        }
        if normalized not in aliases:
            raise ValueError(f"Unsupported coinbase interval: {interval}")
        return aliases[normalized]

    @staticmethod
    def _download_yfinance_symbols_individually(download: Any, symbols: List[str]) -> pd.DataFrame:
        frames: List[pd.DataFrame] = []
        errors: List[str] = []
        for symbol in symbols:
            try:
                raw = download([symbol])
            except Exception as exc:  # pragma: no cover - network dependent
                errors.append(f"{symbol}: {exc}")
                continue
            if raw.empty:
                errors.append(f"{symbol}: empty response")
                continue
            if not isinstance(raw.columns, pd.MultiIndex):
                raw = pd.concat({symbol: raw}, axis=1).swaplevel(0, 1, axis=1)
            frames.append(raw)
        if not frames:
            raise ValueError(
                "yfinance returned no data for symbols="
                + str(symbols)
                + (f"; retries: {'; '.join(errors)}" if errors else "")
            )
        return pd.concat(frames, axis=1)

    @staticmethod
    def _read_wide_market_frame(path: Path, *, time_column: str) -> pd.DataFrame:
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            frame = pd.read_parquet(path)
        elif suffix == ".csv":
            frame = pd.read_csv(path)
        else:
            raise ValueError(f"Unsupported multi-asset market data format: {path.suffix}")
        if isinstance(frame.index, pd.DatetimeIndex):
            out = frame.copy()
        else:
            column = time_column if time_column in frame.columns else None
            if column is None:
                candidates = [
                    col for col in frame.columns if str(col).lower() in {"time", "date", "datetime"}
                ]
                column = str(candidates[0]) if candidates else str(frame.columns[0])
            out = frame.copy()
            out[column] = pd.to_datetime(out[column], errors="coerce")
            out = out.dropna(subset=[column]).set_index(column)
        out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
        out = out.sort_index()
        return out.apply(pd.to_numeric, errors="coerce")
