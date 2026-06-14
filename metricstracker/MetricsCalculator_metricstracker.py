"""
MetricsCalculator_metricstracker.py

【功能說明】
------------------------------------------------------------
本模組為 Lo2cin4BT 績效分析框架的績效指標計算核心模組，負責計算各種交易績效指標，包括收益率、風險指標、夏普比率、最大回撤等，提供完整的績效評估。

【流程與數據流】
------------------------------------------------------------
- 由 BaseMetricTracker 調用，對交易記錄進行績效指標計算
- 計算結果傳遞給 MetricsExporter 進行導出
- 主要數據流：

```mermaid
flowchart TD
    A[BaseMetricTracker] -->|調用| B[MetricsCalculator]
    B -->|計算績效| C[績效指標]
    B -->|傳遞結果| D[MetricsExporter]
```

【維護與擴充重點】
------------------------------------------------------------
- 新增績效指標、計算邏輯時，請同步更新 calculate_metrics/頂部註解
- 若績效指標結構有變動，需同步更新 MetricsExporter、BaseMetricTracker 等依賴模組
- 績效指標計算邏輯如有調整，請於 README 詳列
- 新增/修改績效指標、計算邏輯時，務必同步更新本檔案與所有依賴模組
- 績效指標定義需與業界標準保持一致

【常見易錯點】
------------------------------------------------------------
- 績效指標計算邏輯錯誤會導致結果不準確
- 數據結構變動會影響計算結果
- 績效指標定義不一致會影響比較分析

【錯誤處理】
------------------------------------------------------------
- 數據缺失時提供詳細診斷
- 計算異常時提供修正建議
- 指標定義錯誤時提供標準參考

【範例】
------------------------------------------------------------
- 計算績效指標：calculator = MetricsCalculator(); metrics = calculator.calculate_metrics(df)
- 計算特定指標：calculate_sharpe_ratio(returns)

【與其他模組的關聯】
------------------------------------------------------------
- 由 BaseMetricTracker 調用，績效指標傳遞給 MetricsExporter
- 績效指標結構依賴 MetricsExporter

【版本與變更記錄】
------------------------------------------------------------
- v1.0: 初始版本，支援基本績效指標
- v1.1: 新增風險調整指標
- v1.2: 新增多維度績效分析

【參考】
------------------------------------------------------------
- 詳細績效指標定義請參閱 README
- 其他模組如有依賴本模組，請於對應檔案頂部註解標明
"""

import numpy as np
import pandas as pd
from numba import njit


@njit(cache=True)
def _average_drawdown_numba(drawdown):
    n = len(drawdown)
    if n == 0:
        return 0.0
    in_drawdown = False
    current_min = 0.0
    total = 0.0
    count = 0
    for i in range(n):
        value = drawdown[i]
        if np.isnan(value):
            continue
        if value < 0.0:
            if not in_drawdown:
                in_drawdown = True
                current_min = value
            elif value < current_min:
                current_min = value
        elif in_drawdown:
            total += current_min
            count += 1
            in_drawdown = False
            current_min = 0.0
    if in_drawdown:
        total += current_min
        count += 1
    if count == 0:
        return 0.0
    return total / count


@njit(cache=True)
def _max_consecutive_negative_numba(values):
    max_count = 0
    count = 0
    for i in range(len(values)):
        value = values[i]
        if np.isnan(value):
            continue
        if value < 0.0:
            count += 1
            if count > max_count:
                max_count = count
        else:
            count = 0
    return max_count


@njit(cache=True)
def _max_nonzero_run_ratio_numba(values):
    n = len(values)
    if n == 0:
        return np.nan
    max_run = 0
    run = 0
    for i in range(n):
        value = values[i]
        if np.isnan(value):
            run = 0
            continue
        if value != 0.0:
            run += 1
            if run > max_run:
                max_run = run
        else:
            run = 0
    return max_run / n


class MetricsCalculatorMetricTracker:
    def __init__(self, df, time_unit, risk_free_rate):
        self.df = df
        self.time_unit = time_unit
        self.risk_free_rate = risk_free_rate
        self.strategy_equity = pd.to_numeric(self.df["Equity_value"], errors="coerce")
        self.bah_equity = pd.to_numeric(
            self.df.get("BAH_Equity", self.df["Equity_value"]), errors="coerce"
        )
        self.strategy_returns = self._build_equity_returns("Equity_value")
        self.bah_returns = self._build_equity_returns("BAH_Equity")
        self.strategy_drawdown = self._build_drawdown(self.strategy_equity)
        self.bah_drawdown = pd.to_numeric(
            self.df.get("BAH_Drawdown", self._build_drawdown(self.bah_equity)),
            errors="coerce",
        )
        self.trade_actions = self.df.get("Trade_action")
        self.trade_returns = pd.to_numeric(
            self.df.get("Trade_return", pd.Series(dtype=float)), errors="coerce"
        )
        self.position_size = pd.to_numeric(
            self.df.get("Position_size", pd.Series(dtype=float)), errors="coerce"
        )
        self.closed_trade_returns = (
            pd.to_numeric(
                self.df.loc[self.trade_actions == 4, "Trade_return"], errors="coerce"
            )
            if self.trade_actions is not None and "Trade_return" in self.df.columns
            else pd.Series(dtype=float)
        )
        self.daily_returns = self.strategy_returns  # NOTE: translated to English.
        # NOTE: translated to English.
        # NOTE: translated to English.
        total_periods = len(self.df)
        self.years = total_periods / self.time_unit
        if self.years <= 0:
            self.years = 1.0
        # NOTE: translated to English.

    def _get_data_source(self, source_type="strategy"):
        """獲取數據源（策略或BAH）"""
        if source_type == "bah":
            return {
                "equity": self.bah_equity,
                "returns": self.bah_returns,
                "drawdown": self.bah_drawdown,
            }
        else:
            return {
                "equity": self.strategy_equity,
                "returns": self.strategy_returns,
                "drawdown": self.strategy_drawdown,
            }

    def _build_equity_returns(self, column_name):
        """Build period returns from the equity curve instead of using trade-state fields."""
        if column_name not in self.df.columns:
            return pd.Series(np.zeros(len(self.df)), index=self.df.index, dtype=float)

        equity = pd.to_numeric(self.df[column_name], errors="coerce")
        if equity.isna().all():
            return pd.Series(np.zeros(len(self.df)), index=self.df.index, dtype=float)

        returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        return returns.astype(float)

    def _build_drawdown(self, equity):
        roll_max = equity.cummax()
        return (equity - roll_max) / roll_max

    @staticmethod
    def _average_drawdown_from_series(drawdown_series):
        drawdown = np.asarray(drawdown_series, dtype=np.float64)
        return float(_average_drawdown_numba(drawdown))

    @staticmethod
    def _max_consecutive_negative_count(values):
        arr = np.asarray(values, dtype=np.float64)
        return int(_max_consecutive_negative_numba(arr))

    @staticmethod
    def _max_nonzero_run_ratio(values):
        arr = np.asarray(values, dtype=np.float64)
        if arr.size == 0:
            return None
        value = float(_max_nonzero_run_ratio_numba(arr))
        return None if np.isnan(value) else value

    def _calculate_total_return(self, source_type="strategy"):
        """總回報率計算"""
        data = self._get_data_source(source_type)
        equity = data["equity"]
        return equity.iloc[-1] / equity.iloc[0] - 1

    def _calculate_annualized_return(self, source_type="strategy"):
        """年化回報率計算"""
        tr = self._calculate_total_return(source_type)
        return self._safe_power(1 + tr, 1 / self.years) - 1

    def _calculate_cagr(self, source_type="strategy"):
        """CAGR計算"""
        data = self._get_data_source(source_type)
        equity = data["equity"]
        return self._safe_power(equity.iloc[-1] / equity.iloc[0], 1 / self.years) - 1

    def _calculate_std(self, source_type="strategy"):
        """標準差計算"""
        data = self._get_data_source(source_type)
        returns = data["returns"]
        return np.std(returns, ddof=1)

    def _calculate_annualized_std(self, source_type="strategy"):
        """年化標準差計算"""
        std = self._calculate_std(source_type)
        return std * self._safe_sqrt(self.time_unit)

    def _calculate_downside_risk(self, source_type="strategy", target=0):
        """下行風險計算"""
        data = self._get_data_source(source_type)
        returns = data["returns"]
        downside = returns[returns < target]
        if len(downside) == 0:
            return 0.0
        return self._safe_sqrt(np.mean((downside - target) ** 2))

    def _calculate_annualized_downside_risk(self, source_type="strategy", target=0):
        """年化下行風險計算"""
        downside = self._calculate_downside_risk(source_type, target)
        return downside * self._safe_sqrt(self.time_unit)

    def _calculate_max_drawdown(self, source_type="strategy"):
        """最大回撤計算"""
        data = self._get_data_source(source_type)
        if source_type == "bah" and data["drawdown"] is not None:
            return data["drawdown"].min()

        drawdown = data["drawdown"]
        return drawdown.min()

    def _calculate_recovery_factor(self, source_type="strategy"):
        """恢復因子計算"""
        tr = self._calculate_total_return(source_type)
        mdd = abs(self._calculate_max_drawdown(source_type))
        if mdd == 0:
            return np.nan
        return self._safe_division(tr, mdd)

    def _calculate_sharpe(self, source_type="strategy"):
        """夏普比率計算"""
        data = self._get_data_source(source_type)
        returns = data["returns"]
        mean = returns.mean()
        std = returns.std(ddof=1)
        rf = self.risk_free_rate / self.time_unit
        if std == 0:
            return np.nan
        return self._safe_division(mean - rf, std) * self._safe_sqrt(self.time_unit)

    def _calculate_sortino(self, source_type="strategy"):
        """索提諾比率計算"""
        data = self._get_data_source(source_type)
        returns = data["returns"]
        mean = returns.mean()
        downside = self._calculate_downside_risk(source_type)
        rf = self.risk_free_rate / self.time_unit
        if downside == 0:
            return np.nan
        return self._safe_division(mean - rf, downside) * self._safe_sqrt(
            self.time_unit
        )

    def _calculate_calmar(self, source_type="strategy"):
        """卡爾馬比率計算"""
        ann = self._calculate_annualized_return(source_type)
        rf_year = self.risk_free_rate
        mdd = abs(self._calculate_max_drawdown(source_type))
        if mdd == 0:
            return np.nan
        return self._safe_division(ann - rf_year, mdd)

    def total_return(self):
        # NOTE: translated to English.
        return self._calculate_total_return("strategy")

    def annualized_return(self):
        # NOTE: translated to English.
        return self._calculate_annualized_return("strategy")

    def cagr(self):
        # NOTE: translated to English.
        return self._calculate_cagr("strategy")

    def std(self):
        # NOTE: translated to English.
        return self._calculate_std("strategy")

    def annualized_std(self):
        # NOTE: translated to English.
        return self._calculate_annualized_std("strategy")

    def downside_risk(self, target=0):
        # NOTE: translated to English.
        return self._calculate_downside_risk("strategy", target)

    def annualized_downside_risk(self, target=0):
        # NOTE: translated to English.
        return self._calculate_annualized_downside_risk("strategy", target)

    def max_drawdown(self):
        # NOTE: translated to English.
        return self._calculate_max_drawdown("strategy")

    def average_drawdown(self):
        # NOTE: translated to English.
        return self._average_drawdown_from_series(self.strategy_drawdown)

    def recovery_factor(self):
        # NOTE: translated to English.
        return self._calculate_recovery_factor("strategy")

    """
    def cov(self):
        # 變異係數（以年度報酬率為單位，小數值）
        df = self.df.copy()
        df['year'] = pd.to_datetime(df['Time']).dt.year
        annual_returns = df.groupby('year')['Return'].sum()
        mu = annual_returns.mean()
        sigma = annual_returns.std(ddof=1)
        if mu == 0:
            return np.nan
        return sigma / mu
    """

    def bah_total_return(self):
        return self._calculate_total_return("bah")

    def bah_annualized_return(self):
        return self._calculate_annualized_return("bah")

    def bah_cagr(self):
        return self._calculate_cagr("bah")

    def bah_std(self):
        return self._calculate_std("bah")

    def bah_annualized_std(self):
        return self._calculate_annualized_std("bah")

    def bah_downside_risk(self, target=0):
        return self._calculate_downside_risk("bah", target)

    def bah_annualized_downside_risk(self, target=0):
        return self._calculate_annualized_downside_risk("bah", target)

    def bah_max_drawdown(self):
        if "BAH_Drawdown" in self.df.columns:
            return self.df["BAH_Drawdown"].min()
        bah_equity = self.df["BAH_Equity"]
        roll_max = bah_equity.cummax()
        drawdown = (bah_equity - roll_max) / roll_max
        return drawdown.min()

    def bah_average_drawdown(self):
        return self._average_drawdown_from_series(self.bah_drawdown)

    def bah_recovery_factor(self):
        return self._calculate_recovery_factor("bah")

    def bah_cov(self):
        mu = self.df["BAH_Return"].mean()
        sigma = self.df["BAH_Return"].std(ddof=1)
        if mu == 0:
            return np.nan
        return self._safe_division(sigma, mu)

    def bah_sharpe(self):
        return self._calculate_sharpe("bah")

    def bah_sortino(self):
        return self._calculate_sortino("bah")

    def bah_calmar(self):
        return self._calculate_calmar("bah")

    def sharpe(self):
        return self._calculate_sharpe("strategy")

    def sortino(self):
        return self._calculate_sortino("strategy")

    def calmar(self):
        return self._calculate_calmar("strategy")

    def information_ratio(self):
        """信息比率 (Information Ratio)：衡量策略相對 Buy & Hold 的超額報酬穩定性"""
        strategy = self.strategy_returns
        benchmark = self.bah_returns
        if strategy.empty or benchmark.empty:
            return None
        diff = strategy - benchmark
        mean_excess = diff.mean()
        tracking_error = diff.std(ddof=1)
        if tracking_error == 0:
            return None
        return mean_excess / tracking_error

    def beta(self):
        """Beta：衡量策略與市場（B&H）的相關性和系統性風險敞口"""
        strategy = self.strategy_returns
        benchmark = self.bah_returns
        if strategy.empty or benchmark.empty:
            return None
        x = strategy
        y = benchmark
        cov = np.cov(x, y, ddof=1)[0, 1]
        var = np.var(y, ddof=1)
        if var == 0:
            return None
        return cov / var

    def alpha(self):
        """Alpha：策略相對市場的超額回報，基於CAPM模型"""
        strategy = self.strategy_returns
        benchmark = self.bah_returns
        if strategy.empty or benchmark.empty:
            return None
        rf = (
            self.risk_free_rate / self.time_unit
            if hasattr(self, "risk_free_rate") and hasattr(self, "time_unit")
            else 0
        )
        beta = self.beta()
        mean_return = strategy.mean()
        mean_bah = benchmark.mean()
        if beta is None:
            return None
        return mean_return - (rf + beta * (mean_bah - rf))

    def trade_count(self):
        """交易次數 (Trade_count)：只計算開倉次數 (Trade_action == 1)"""
        if self.trade_actions is None:
            return None
        return int((self.trade_actions == 1).sum())

    def win_rate(self):
        """勝率 (Win_rate)：盈利交易佔總平倉交易的比例"""
        if (
            "Trade_action" not in self.df.columns
            or "Trade_return" not in self.df.columns
        ):
            return None
        closed_trade_returns = self.closed_trade_returns.dropna()
        if len(closed_trade_returns) == 0:
            return None
        wins = (closed_trade_returns > 0).sum()
        return wins / len(closed_trade_returns)

    def profit_factor(self):
        """盈虧比 (Profit_factor)：總盈利除以總虧損"""
        if "Trade_return" not in self.df.columns:
            return None
        trade_returns = self.trade_returns.dropna()
        profits = trade_returns[trade_returns > 0].sum()
        losses = trade_returns[trade_returns < 0].sum()
        if losses == 0:
            return None
        return self._safe_division(profits, abs(losses))

    def avg_trade_return(self):
        """平均交易回報 (Avg_trade_return)：每筆交易的平均收益"""
        if "Trade_return" not in self.df.columns:
            return None
        return self.trade_returns.mean()

    # NOTE: translated to English.

    def max_consecutive_losses(self):
        """最大連續虧損 (Max_consecutive_losses)：連續虧損交易的最大次數"""
        if "Trade_return" not in self.df.columns:
            return None
        return self._max_consecutive_negative_count(self.trade_returns.dropna())

    def exposure_time(self):
        """持倉時間比例 (Exposure_time)：持倉時間佔總時間的比例"""
        if "Position_size" not in self.df.columns:
            return None
        return ((self.position_size != 0).sum() / len(self.position_size) * 100)

    def max_holding_period_ratio(self):
        """最長持倉時間比例 (Max_holding_period_ratio)：單次持倉時間的最長持續時間佔總回測時間的比例"""
        if "Position_size" not in self.df.columns:
            return None
        return self._max_nonzero_run_ratio(self.position_size)

    def calc_strategy_metrics(self):
        return {
            "Total_return": self.total_return(),
            "Annualized_return (CAGR)": self.annualized_return(),
            "Std": self.std(),
            "Annualized_std": self.annualized_std(),
            "Downside_risk": self.downside_risk(),
            "Annualized_downside_risk": self.annualized_downside_risk(),
            "Max_drawdown": self.max_drawdown(),
            "Average_drawdown": self.average_drawdown(),
            "Recovery_factor": self.recovery_factor(),
            "Sharpe": self.sharpe(),
            "Sortino": self.sortino(),
            "Calmar": self.calmar(),
            "Information_ratio": self.information_ratio(),
            "Alpha": self.alpha(),
            "Beta": self.beta(),
            "Trade_count": self.trade_count(),
            "Win_rate": self.win_rate(),
            "Profit_factor": self.profit_factor(),
            "Avg_trade_return": self.avg_trade_return(),
            "Max_consecutive_losses": self.max_consecutive_losses(),
            "Exposure_time": self.exposure_time(),
            "Max_holding_period_ratio": self.max_holding_period_ratio(),
        }

    def calc_bah_metrics(self):
        return {
            "BAH_Total_return": self.bah_total_return(),
            "BAH_Annualized_return (CAGR)": self.bah_annualized_return(),
            "BAH_Std": self.bah_std(),
            "BAH_Annualized_std": self.bah_annualized_std(),
            "BAH_Downside_risk": self.bah_downside_risk(),
            "BAH_Annualized_downside_risk": self.bah_annualized_downside_risk(),
            "BAH_Max_drawdown": self.bah_max_drawdown(),
            "BAH_Average_drawdown": self.bah_average_drawdown(),
            "BAH_Recovery_factor": self.bah_recovery_factor(),
            # 'BAH_Cov': self.bah_cov(),
            "BAH_Sharpe": self.bah_sharpe(),
            "BAH_Sortino": self.bah_sortino(),
            "BAH_Calmar": self.bah_calmar(),
        }

    def _safe_power(self, base, exponent, fallback=0.0):
        """
        安全的冪運算，處理邊界情況
        Args:
            base: 底數
            exponent: 指數
            fallback: 當計算無效時的返回值
        """
        # NOTE: translated to English.
        try:
            # NOTE: translated to English.
            if base <= 0 and exponent <= 0:
                return fallback
            if base == 0 and exponent > 0:
                return 0.0
            if base == 0 and exponent < 0:
                return fallback
            if np.isnan(base) or np.isnan(exponent):
                return fallback
            if np.isinf(exponent) and base == 1:
                return 1.0
            if np.isinf(exponent) and base != 1:
                return fallback

            # NOTE: translated to English.
            if abs(base) < 1e-10 and abs(exponent) > 100:
                return 0.0

            # NOTE: translated to English.
            if abs(base - 1) < 1e-10:
                return 1.0

            # NOTE: translated to English.
            # NOTE: translated to English.
            if abs(exponent) > 1000:
                # NOTE: translated to English.
                try:
                    with np.errstate(over='raise', invalid='raise'):
                        log_result = exponent * np.log(base)
                        # NOTE: translated to English.
                        if log_result > 700:  # NOTE: translated to English.
                            return fallback
                        elif log_result < -700:
                            return 0.0
                        result = np.exp(log_result)
                        if np.isnan(result) or np.isinf(result):
                            return fallback
                        return result
                except (ValueError, OverflowError, FloatingPointError):
                    return fallback

            # NOTE: translated to English.
            if base > 0:  # NOTE: translated to English.
                # NOTE: translated to English.
                with np.errstate(over='ignore', invalid='ignore'):
                    result = np.power(base, exponent)
                    if np.isnan(result) or np.isinf(result):
                        return fallback
                    return result
            else:
                return fallback

        except (ValueError, OverflowError):
            return fallback

    def _safe_sqrt(self, value, fallback=0.0):
        """
        安全的平方根運算
        """
        try:
            if value < 0 or np.isnan(value) or np.isinf(value):
                return fallback
            result = np.sqrt(value)
            if np.isnan(result) or np.isinf(result):
                return fallback
            return result
        except (ValueError, RuntimeWarning):
            return fallback

    def _safe_division(self, numerator, denominator, fallback=0.0):
        """
        安全的除法運算
        """
        try:
            if denominator == 0 or np.isnan(denominator) or np.isinf(denominator):
                return fallback
            if np.isnan(numerator) or np.isinf(numerator):
                return fallback
            result = numerator / denominator
            if np.isnan(result) or np.isinf(result):
                return fallback
            return result
        except (ValueError, RuntimeWarning):
            return fallback
