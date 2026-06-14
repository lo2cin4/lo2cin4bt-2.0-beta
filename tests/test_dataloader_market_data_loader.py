import importlib
from pathlib import Path

import pandas as pd
import pytest


def test_multi_asset_market_data_loader_reads_wide_csv(tmp_path):
    mod = importlib.import_module("dataloader.market_data_loader")
    path = tmp_path / "close.csv"
    pd.DataFrame(
        {
            "Time": ["2024-01-03", "2024-01-02"],
            "AAA": ["101.5", "100.0"],
            "BBB": ["201.5", "200.0"],
        }
    ).to_csv(path, index=False)

    frames = mod.MultiAssetMarketDataLoader(repo_root=Path.cwd()).load(
        {"close": {"path": str(path), "time_column": "Time"}}
    )

    assert list(frames.keys()) == ["close"]
    assert frames["close"].index.strftime("%Y-%m-%d").tolist() == ["2024-01-02", "2024-01-03"]
    assert frames["close"].loc[pd.Timestamp("2024-01-03"), "AAA"] == pytest.approx(101.5)


def test_multi_asset_market_data_loader_dispatches_coinbase(monkeypatch):
    mod = importlib.import_module("dataloader.market_data_loader")
    calls = []

    def fake_download(self, spec):
        calls.append(spec)
        return {"close": pd.DataFrame({"BTC-USD": [100.0]}, index=[pd.Timestamp("2024-01-01")])}

    monkeypatch.setattr(mod.MultiAssetMarketDataLoader, "_download_coinbase", fake_download)

    frames = mod.MultiAssetMarketDataLoader(repo_root=Path.cwd()).load(
        {"provider": "coinbase", "symbols": ["BTC-USD"]}
    )

    assert calls and calls[0]["provider"] == "coinbase"
    assert frames["close"].loc[pd.Timestamp("2024-01-01"), "BTC-USD"] == pytest.approx(100.0)


def test_market_data_loader_runtime_cache_round_trips_frames(tmp_path):
    mod = importlib.import_module("dataloader.market_data_loader")
    loader = mod.MultiAssetMarketDataLoader(repo_root=tmp_path)
    cache_path = loader._market_cache_path(  # pylint: disable=protected-access
        {
            "provider": "yfinance",
            "symbols": ["QQQ"],
            "start": "2024-01-01",
            "end": None,
            "interval": "1d",
        }
    )
    frames = {
        "open": pd.DataFrame({"QQQ": [100.0, 101.0]}, index=pd.date_range("2024-01-01", periods=2)),
        "close": pd.DataFrame({"QQQ": [100.5, 101.5]}, index=pd.date_range("2024-01-01", periods=2)),
    }

    loader._write_market_cache(cache_path, frames)  # pylint: disable=protected-access
    cached = loader._read_market_cache(cache_path, max_age_seconds=3600)  # pylint: disable=protected-access

    assert set(cached) == {"open", "close"}
    assert cached["close"].loc[pd.Timestamp("2024-01-02"), "QQQ"] == pytest.approx(101.5)


def test_coinbase_symbol_download_normalizes_ohlcv():
    mod = importlib.import_module("dataloader.market_data_loader")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                [1704153600, "90", "110", "100", "105", "12.5"],
                [1704240000, "95", "115", "105", "111", "9.0"],
            ]

    class FakeRequests:
        @staticmethod
        def get(url, params, timeout):
            assert url.endswith("/products/BTC-USD/candles")
            assert params["granularity"] == 86400
            assert timeout == 7
            return FakeResponse()

    frame = mod.MultiAssetMarketDataLoader._download_coinbase_symbol(
        requests_module=FakeRequests,
        api_base="https://example.test",
        symbol="BTC-USD",
        granularity=86400,
        start="2024-01-02",
        end="2024-01-04",
        timeout=7,
    )

    assert frame.index.strftime("%Y-%m-%d").tolist() == ["2024-01-02", "2024-01-03"]
    assert frame.loc[pd.Timestamp("2024-01-02"), "close"] == pytest.approx(105.0)
    assert frame.loc[pd.Timestamp("2024-01-03"), "volume"] == pytest.approx(9.0)


@pytest.mark.parametrize("provider", ["futu", "ibkr"])
def test_multi_asset_market_data_loader_validates_broker_provider_symbols(provider):
    mod = importlib.import_module("dataloader.market_data_loader")

    with pytest.raises(ValueError, match=f"provider={provider}"):
        mod.MultiAssetMarketDataLoader(repo_root=Path.cwd()).load({"provider": provider})


def test_futu_loader_symbol_mapping_without_gateway_import():
    mod = importlib.import_module("dataloader.futu_loader")

    assert mod.FutuMarketDataLoader._to_futu_code("QQQ", "US") == "US.QQQ"
    assert mod.FutuMarketDataLoader._to_futu_code("HK.00700", "US") == "HK.00700"


def test_ibkr_loader_bar_size_mapping():
    mod = importlib.import_module("dataloader.ibkr_loader")

    assert mod.IBKRMarketDataLoader._bar_size("1d") == "1 day"
    assert mod.IBKRMarketDataLoader._bar_size("5m") == "5 mins"


def test_legacy_binance_loader_uses_symbol_attribute_without_name_error(monkeypatch):
    mod = importlib.import_module("dataloader.binance_loader")

    class FakeClient:
        def get_historical_klines(self, symbol, interval, start_date, end_date):
            assert symbol == "BTCUSDT"
            assert interval == "1d"
            return []

    monkeypatch.setattr(mod, "Client", lambda: FakeClient())
    loader = mod.BinanceLoader()
    loader.symbol = "BTCUSDT"
    loader.interval = "1d"
    loader.start_date = "2024-01-01"
    loader.end_date = "2024-01-02"

    data, frequency = loader.load()

    assert data is None
    assert frequency == "1d"


def test_legacy_coinbase_loader_uses_symbol_attribute_without_name_error(monkeypatch):
    mod = importlib.import_module("dataloader.coinbase_loader")

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return []

    monkeypatch.setattr(mod.requests, "get", lambda url, params: FakeResponse())
    loader = mod.CoinbaseLoader()
    loader.symbol = "BTC-USD"
    loader.interval = "1d"
    loader.start_date = "2024-01-01"
    loader.end_date = "2024-01-02"

    data, frequency = loader.load()

    assert data is None
    assert frequency == "1d"
