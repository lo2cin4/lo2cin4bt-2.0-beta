# metricstracker 開發者說明文件

## 模組概覽（Module Overview）

**metricstracker** 是 lo2cin4bt 量化回測框架中的獨立績效分析模組，負責回測結果的績效指標計算與標準化資料管理。
本模組僅透過 DataFrame 及 Parquet 等標準格式與其他模組交互，確保低耦合、高可維護性。

- **輸入來源**：回測產生的 Parquet 檔案（含交易記錄與 metadata）
- **輸出目標**：新增績效指標 metadata 的 Parquet 檔案，供後續分析或前端使用

---

## 開發目標（Development Goals）

- 計算和輸入每日回測表現數據，如，並創建新的 parquet 檔案
- 提供標準化的回測績效指標計算（如總回報、年化回報、最大回撤等），並輸入至metadata 內
- 支援批量回測結果的自動匯入、選擇與分析
- 僅透過標準格式（DataFrame/Parquet）與外部模組溝通
- 方便擴充每日指標、策略指標等多層次績效分析
- 保持模組化、低耦合、易於單元測試與維護

---

## 專案結構（Project Structure）

```plaintext
metricstracker/
├── __init__.py                    # 模組初始化
├── Base_metricstracker.py         # 績效分析基底類
├── DataImporter_metricstracker.py # Parquet 檔案選擇與匯入
├── MetricsCalculator_metricstracker.py # 核心績效指標計算器
├── README.md                      # 本文件
```

- **Base_metricstracker.py**：定義績效分析基底類與標準介面
- **DataImporter_metricstracker.py**：用戶互動式選擇、匯入 Parquet 檔案
- **MetricsCalculator_metricstracker.py**：計算各類績效指標並寫入 Parquet metadata

---

## 核心模組功能（Core Components）

### 1. Base_metricstracker.py

- **功能**：定義績效分析的標準介面與基底類
- **主要處理**：規範 analyze、load_data、calculate_metrics、export 等方法
- **輸入**：Parquet 檔案路徑或 DataFrame
- **輸出**：標準化 DataFrame 或分析結果

### 2. DataImporter_metricstracker.py

- **功能**：互動式列出、選擇回測 Parquet 檔案
- **主要處理**：掃描指定資料夾、支援多選/全選、回傳檔案路徑
- **輸入**：目錄路徑
- **輸出**：所選 Parquet 檔案路徑 list

### 3. MetricsCalculator_metricstracker.py

- **功能**：計算回測績效指標，並將結果寫入 Parquet metadata
- **主要處理**：自動偵測年數，計算總回報、年化回報、CAGR、標準差、最大回撤等 summary 指標
- **輸入**：回測 DataFrame、時間單位、無風險利率
- **輸出**：含指標 metadata 的 Parquet 檔案

---

## 輸入輸出規格（Input and Output Specifications）

### 輸入

- **來源**：`records/backtester/` 目錄下的 Parquet 檔案
- **關鍵欄位**：
  - `Time`（datetime）：交易日期
  - `Equity_value`（float）：每日資產淨值
  - `Change`（float）：每日報酬率（百分比）
  - 其他交易記錄欄位
  - 用戶輸入的時間單位、

### 輸出

- **格式**：Parquet 檔案，存於 `records/metricstracker/`
- **內容**：
  - DataFrame：原始交易記錄（可擴充每日指標欄位）
  - Metadata：新增 `strategy_metrics1`、`bah_metrics1` 等績效指標（JSON 字串）

### 欄位映射（如需）

| 輸入欄位         | 輸出欄位/用途         |
|------------------|----------------------|
| Time             | 用於年數自動偵測     |
| Change           | 用於績效指標計算     |
| Equity_value     | 用於回報/回撤計算    |

---

## 績效指標定義（Performance Metrics）

（以下新增各指標常見數值區間與評價標準，並說明負數意義）

### 1. 收益相關指標

- **總回報率 (Total_return)**：策略從開始到結束的累計回報百分比，反映整體收益。
  - 公式：Total_return = (Equity_value.iloc[-1] / Equity_value.iloc[0]) - 1
  - **常見標準**：>0 為正報酬，<0 為虧損。數值愈高愈好。
- **複合年增長率 (Annualized_return(CAGR))**：考慮複利效應的年化回報率，衡量長期收益穩定性。
  - 公式：CAGR = [(Equity_value.iloc[-1] / Equity_value.iloc[0])^(1 / 年數) - 1]
  - **常見標準**：>10% 為合格，>20% 為良好，>30% 為優秀。<0 為長期虧損。
- **標準差 (Std)**：回報序列的標準差，衡量整個回測期間收益波動的整體水平。
  - **常見標準**：無絕對標準，需與年化報酬率搭配觀察。
- **年化標準差 (Annualized_std)**：回報序列的年化標準差，衡量收益的年化波動程度。
  - 公式：Std × 平方根(時間單位)
  - **常見標準**：愈低愈穩定，但過低可能代表策略不活躍。
- **下行風險 (Downside_risk)**：僅考慮負收益或低於目標回報的波動性。
  - **常見標準**：愈低愈好。
- **年化下行風險 (Annualized_downside_risk)**：僅考慮負收益的年化波動性。
  - 公式：Downside_risk × 平方根(時間單位)
  - **常見標準**：愈低愈好。
- **最大回撤百分比 (Max_drawdown)**：策略淨值從峰值到谷底的最大跌幅。
  - 公式：max((峰值 - 谷底) / 峰值)
  - **常見標準**：>-10% 極佳，>-20% 良好，>-30% 合格，<-30% 風險偏高。
  - **負數意義**：數值越負代表最大損失越大。
- **平均回撤 (Average_drawdown)**：回撤的平均幅度。
  - **常見標準**：愈接近0愈好。
- **恢復因子 (Recovery_factor)**：總回報率除以最大回撤百分比。
  - 公式：Recovery_factor = Total_return / Max_drawdown
  - **常見標準**：>1 合格，>2 良好，>3 優秀。負數代表長期虧損。

### 2. 風險調整後收益指標

#### 年化夏普比率 (Sharpe)

- **定義**：衡量每單位年化波動率的超額回報，標準化為年化形式。
- **用途**：評估策略在承擔風險後的收益效率，廣泛用於比較不同策略。
- **公式**：
  - Sharpe = (Mean_return - Risk_free_rate) / Std × 平方根(年化時間單位)
- **常見標準**：
  - <1 不合格（風險大於報酬）
  - 1~2 合格
  - 2~2.5 良好
  - ≥2.5 優秀
- **負數意義**：代表策略長期下來報酬率低於無風險利率，或風險過大。

#### 索提諾比率 (Sortino)

- **定義**：類似夏普比率，但僅用下行波動率計算，專注於不利風險。
- **公式**：
  - Sortino = (Mean_return - Risk_free_rate) / Downside_risk × 平方根(年化時間單位)
- **常見標準**：
  - <1 不合格
  - 1~2 合格
  - 2~2.5 良好
  - ≥2.5 優秀
- **負數意義**：代表下行風險過大或報酬率過低。

#### 卡爾瑪比率 (Calmar)

- **定義**：年化回報率減去無風險利率後，再除以最大回撤，衡量超額回報相對最大損失的效率。
- **公式**：
  - Calmar = (Annualized_return - risk_free_rate) / abs(Max_drawdown)
- **常見標準**：
  - <0.5 不合格
  - 0.5~1 合格
  - 1~2 良好
  - ≥2 優秀
- **負數意義**：代表最大回撤過大或年化報酬率為負。

#### 信息比率 (Information_ratio)

- **定義**：(策略回報 - 基準回報)除以跟踪誤差，衡量相對基準的超額回報穩定性。
- **用途**：評估策略相對於基準（如指數）的表現。
- **公式**：
  Information_ratio = (Mean_return - BAH_Return.mean()) / (Return - BAH_Return).std()
- **常見標準**：
  - <0 不合格
  - 0~0.5 合格
  - 0.5~1 良好
  - ≥1 優秀
- **負數意義**：代表策略長期表現不如基準。

#### Alpha

- **定義**：策略相對市場的超額回報，基於CAPM模型。
- **用途**：衡量策略創造的獨立於市場的收益。
- **公式**：
  Alpha = Return.mean() - [Risk_free_rate + (cov(Return, BAH_Return) / var(BAH_Return)) * (BAH_Return.mean() - Risk_free_rate)]
- **常見標準**：
  - <0 不合格
  - 0~0.05 合格
  - 0.05~0.1 良好
  - ≥0.1 優秀
- **負數意義**：代表策略長期表現不如市場。

#### Beta

- **定義**：衡量策略與市場的相關性和系統性風險敞口。
- **用途**：反映策略對市場波動的敏感度。
- **公式**：
  Beta = cov(Return, BAH_Return) / var(BAH_Return)
- **常見標準**：
  - 0~0.5 低相關
  - 0.5~1.5 市場型
  - ≥1.5 高風險型
- **負數意義**：代表與市場呈反向關係。

### 3. 交易效率指標

#### 交易次數 (Trade_count)

- **定義**：只計算開倉次數，不包括平倉。
- **用途**：評估策略的交易頻率。
- **常見標準**：依策略設計而異，過高可能代表過度交易。

#### 勝率 (Win_rate)

- **定義**：盈利交易佔總交易的比例。
- **用途**：評估策略的交易準確性，需結合策略賠率觀察。
- **常見標準**：
  - <0.3 勝率偏低
  - 0.3~0.5 合格
  - 0.5~0.7 良好
  - ≥0.7 優秀
- **負數意義**：無（勝率不會為負）。

#### 盈虧比 (Profit_factor)

- **定義**：總盈利除以總虧損。
- **用途**：反映策略的整體盈利效率。
- **公式**：
  Profit_factor = Trade_return[Trade_return > 0].sum() / abs(Trade_return[Trade_return < 0].sum())
- **常見標準**：
  - <1 不合格（賠多賺少）
  - 1~1.5 合格
  - 1.5~2 良好
  - ≥2 優秀
- **負數意義**：理論上不會為負，若為負代表計算異常。

#### 平均交易回報 (Avg_trade_return)

- **定義**：每筆交易的平均收益。
- **用途**：衡量單筆交易的期望收益。
- **常見標準**：>0 為正期望，<0 為負期望。

#### 期望值 (Expectancy)

- **定義**：每筆交易的期望收益，考慮勝率與盈虧幅度。
- **用途**：評估策略的長期盈利潛力。
- **公式**：
  Expectancy = (Win_rate × 平均盈利) - (敗率 × abs(平均虧損))
  敗率 = 1 - 勝率
- **常見標準**：
  - <0 不合格（長期下來每做一筆交易平均會虧損）
  - 0~0.1 合格
  - 0.1~0.2 良好
  - ≥0.2 優秀
- **負數意義**：代表策略長期下來每做一筆交易平均會虧損，通常是勝率低或平均虧損遠大於平均獲利。

#### 最大連續虧損 (Max_consecutive_losses)

- **定義**：連續虧損交易的最大次數或金額。
- **用途**：評估策略在最差情況下的抗壓能力。
- **常見標準**：依策略設計而異，愈小愈好。
- **負數意義**：無（次數不會為負）。

#### 持倉時間比例 (Exposure_time)

- **定義**：持倉時間佔總時間的比例。
- **用途**：衡量資金在市場中的暴露程度。
- **常見標準**：依策略設計而異。

#### 最長持倉時間比例 (Max_holding_period_ratio)

- **定義**：單次持倉時間的最長持續時間佔總回測時間的比例。
- **用途**：評估單次交易的最大市場暴露時間。
- **常見標準**：依策略設計而異。

---

## 目前進度（Current Progress）

### ✅ 已完成

- Parquet 檔案多選/全選匯入流程
- 年數自動偵測（依據 Time 欄位）
- 標準化績效指標計算主體
- 將一些每日指標（如 drawdown、B&H equity curve）自動寫入parquet的主表格內
- 寫入後output出_metrics.parquet
- 完整低耦合、模組化設計
- 每日指標（如 drawdown、B&H equity curve）已完整自動寫入 parquet 主表格內

### 🔄 進行中

---

## 疑難排解（持續更新）

1. 每日指標尚未寫入 DataFrame 22/07/2025
問題詳情：目前僅 summary 指標寫入 metadata，DataFrame 未自動新增每日指標欄位。
解決方法：設計中，預計支援 rolling/mdd 等每日欄位。

2. pandas 讀取 Parquet 無法直接看到 metadata 22/07/2025
問題詳情：pandas 讀 parquet 只見主表，metadata 需用 pyarrow 讀取。
解決方法：建議用 pyarrow 檢查指標。

---

## 開發工作流程（Development Workflow）

### 運行/測試範例

```bash
# 互動式選擇檔案並計算指標
python main.py
# 或直接執行 DataImporter
python metricstracker/DataImporter_metricstracker.py
```

### 輸出路徑

- `records/metricstracker/xxx_metrics.parquet`：含指標 metadata 的新檔案

### 環境需求

- Python 3.9+
- 依賴：pandas, numpy, pyarrow

---

## 欄位命名規範

- 所有主表格欄位，第一個字母大寫，其餘小寫。
- 兩個字以上的欄位用 _連接（如 Position_size, Trade_action, Equity_value）。
- meta 欄位首字母大寫。
- 請全流程、所有模組、所有導出/驗證/分析皆遵循此規範。

---

## 技術備註（Technical Notes）

- **指標欄位定義**（summary，寫入 metadata）：
  - total_return,annualized_return(cagr), std, annualized_std, downside_risk, annualized_downside_risk, max_drawdown, average_drawdown, recovery_factor, cov
- **資料型別**：
  - `Time`：datetime64[ns]
  - `Change`、`Equity_value`：float64
- **Parquet metadata**：
  - 以 JSON 字串寫入，key 為 `strategy_metrics1`、`bah_metrics1` 等

---

## 聯繫方式（Contact）

Telegram: [https://t.me/lo2cin4_jesse](https://t.me/lo2cin4_jesse)

---

如需協助或有建議，歡迎隨時聯絡！

---

## Summary 指標公式與主表格欄位對照表

| 指標名稱                | 公式（以主表格欄位表示）                                                                 | 需用到的主表格欄位         | 主表格是否已有欄位 |
|------------------------|----------------------------------------------------------------------------------------|----------------------------|-------------------|
| 總回報率 (Total_return) | (Equity_value.iloc[-1] / Equity_value.iloc[0]) - 1                                     | Equity_value               | ✅                |
| BAH_總回報率 (BAH_Total_return) | (BAH_Equity.iloc[-1] / BAH_Equity.iloc[0]) - 1                                     | BAH_Equity               | ✅                |
| 複合年增長率 (Annualized_return(CAGR))     | pow(Equity_value.iloc[-1] / Equity_value.iloc[0], 1 / 年數) - 1                        | Equity_value               | ✅                |
| BAH_複合年增長率 (BAH_Annualized_return(CAGR)) | pow(BAH_Equity.iloc[-1] / BAH_Equity.iloc[0], 1 / 年數) - 1                      | BAH_Equity               | ✅                |
| 標準差 (Std)            | Return.std()                                                                           | Return                     | ✅                |
| BAH_標準差 (BAH_Std)            | BAH_Return.std()                                                                           | BAH_Return                     | ✅                |
| 年化標準差 (Annualized_std) | Return.std() * sqrt(時間單位)                                                        | Return                     | ✅                |
| BAH_年化標準差 (BAH_Annualized_std) | BAH_Return.std() * sqrt(時間單位)                                                        | BAH_Return                     | ✅                |
| 下行風險 (Downside_risk)| Return[Return < 0] 的標準差                                                            | Return                     | ✅                |
| BAH_下行風險 (BAH_Downside_risk)| BAH_Return[BAH_Return < 0] 的標準差                                                            | BAH_Return                     | ✅                |
| 年化下行風險 (Annualized_downside_risk) | Downside_risk * sqrt(時間單位)                                              | Return                     | ✅                |
| BAH_年化下行風險 (BAH_Annualized_downside_risk) | BAH_Downside_risk * sqrt(時間單位)                                              | BAH_Return                     | ✅                |
| 最大回撤 (Max_drawdown) | (Equity_value - Equity_value.cummax()) / Equity_value.cummax() 的最小值                | Equity_value               | ✅                |
| BAH_最大回撤 (BAH_Max_drawdown) | (BAH_Equity - BAH_Equity.cummax()) / BAH_Equity.cummax() 的最小值                | BAH_Equity, BAH_Drawdown               | ✅ |
| 平均回撤 (Average_drawdown) | (Equity_value - Equity_value.cummax()) / Equity_value.cummax() 的平均值            | Equity_value               | ✅                |
| BAH_平均回撤 (BAH_Average_drawdown) | (BAH_Equity - BAH_Equity.cummax()) / BAH_Equity.cummax() 的平均值            | BAH_Equity, BAH_Drawdown               | ✅ |
| 恢復因子 (Recovery_factor) | Total_return / abs(Max_drawdown)                                                      | Equity_value               | ✅                |
| BAH_恢復因子 (BAH_Recovery_factor) | BAH_Total_return / abs(BAH_Max_drawdown)                                                      | BAH_Equity, BAH_Drawdown               | ✅ |
| 年化夏普比率 (Sharpe)   | (Return.mean() - risk_free_rate) / Return.std() * sqrt(時間單位)                      | Return                     | ✅                |
| BAH_年化夏普比率 (BAH_Sharpe)   | (BAH_Return.mean() - risk_free_rate) / BAH_Return.std() * sqrt(時間單位)                      | BAH_Return                     | ✅                |
| 年化索提諾比率 (Sortino)| (Return.mean() - risk_free_rate) / Downside_risk * sqrt(時間單位)                     | Return                     | ✅                |
| BAH_年化索提諾比率 (BAH_Sortino)| (BAH_Return.mean() - risk_free_rate) / BAH_Downside_risk * sqrt(時間單位)                     | BAH_Return                     | ✅                |
| 卡爾瑪比率 (Calmar)     | (Annualized_return - risk_free_rate) / abs(Max_drawdown)                                                         | Equity_value               | ✅                |
| BAH_卡爾瑪比率 (BAH_Calmar)     | (BAH_Annualized_return - risk_free_rate) / abs(BAH_Max_drawdown)                                                         | BAH_Equity, BAH_Drawdown               | ✅ |
| 信息比率 (Information_ratio) | (Return.mean() - BAH_Return.mean()) / (Return - BAH_Return).std()                  | Return, BAH_Return         | ✅                |
| Alpha                   | Return.mean() - [Risk_free_rate + (cov(Return, BAH_Return) / var(BAH_Return)) * (BAH_Return.mean() - Risk_free_rate)]         | Return, BAH_Return, Risk_free_rate         | ✅                |
| Beta                    | cov(Return, BAH_Return) / var(BAH_Return)                                              | Return, BAH_Return         | ✅                |
| 交易次數 (Trade_count)  | (Trade_action == 1).sum()                                                              | Trade_action               | ✅                |
| 勝率 (Win_rate)         | (Trade_return > 0).sum() / (Trade_action == 4).sum()                                   | Trade_return, Trade_action | ✅                |
| 盈虧比 (Profit_factor)  | Trade_return[Trade_return > 0].sum() / abs(Trade_return[Trade_return < 0].sum())       | Trade_return               | ✅                |
| 平均交易回報 (Avg_trade_return) | Trade_return.mean()                                                            | Trade_return               | ✅                |
| 期望值 (Expectancy)     | (Win_rate × 平均盈利) - (敗率 × abs(平均虧損))                                            | Win_rate, 敗率, 平均盈利, 平均虧損         | ✅                |
| 最大連續虧損 (Max_consecutive_losses) | 連續 Trade_return < 0 的最大次數或金額                                 | Trade_return               | ✅                |
| 持倉時間比例 (Exposure_time) | (Position_size != 0).sum() / len(Position_size) * 100                              | Position_size              | ✅                |
| 最長持倉時間比例 (Max_holding_period_ratio)        | max(單次持倉時間) / 總時間 * 100                                                      | Holding_period             | ✅                |

---

**備註：**

- 以上 BAH_最大回撤 (BAH_Max_drawdown)、BAH_平均回撤 (BAH_Average_drawdown)、BAH_恢復因子 (BAH_Recovery_factor)、BAH_卡爾瑪比率 (BAH_Calmar) 等 summary 指標需每日記錄 BAH_Drawdown 欄位。
- 其餘主表格每日欄位已齊全。

**最新狀態說明：**

- 所有 summary 指標公式皆可直接用主表格欄位（如 Return, BAH_Return）即時計算，無需額外存 summary 欄位（如 Mean_bah_return、Tracking_error、Beta）。
- 上表所有 summary 指標所需主表格欄位現已齊全，無需再補每日欄位。
- 未來如有新 summary 指標，請先於主表格自動新增每日欄位，再於本表補充。

**如何驗證：**

- 建議直接執行 main.py，觀察 debug print 是否所有 summary 指標都能順利產生，或有無缺欄位錯誤訊息。
- 若有缺欄位錯誤，請依據本表補齊主表格每日欄位。
