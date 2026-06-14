# Workspace Guide

## 繁體中文

`workspace/` 是用戶和 AI 的安全工作區。正常研究策略時，只需要在這裡放資料、策略設定、WFA 設定、自訂指標和 AI 筆記。

在未充分了解前，不要手動改核心程式資料夾，例如 `app/`、`backtester/`、`dataloader/`、`autorunner/`、`wfanalyser/`、`metricstracker/` 或 `plotter/`。

### 這不是自動識別資料夾

AI 不應看到一份 CSV/JSON 就假裝可以使用。檔案只有在符合合約，或被 `workspace/runs/` 設定檔明確引用時，才會被系統調動。

### 資料夾用途

| 資料夾 | 用途 | 何時會被使用 |
| --- | --- | --- |
| `workspace/datasets/` | 你的 CSV、Excel、Parquet 資料 | 被 `runs/` 設定檔或 `features/` 特徵合約引用 |
| `workspace/features/` | 外部資料如何安全接到價格資料的特徵合約 | 被 `runs/` 設定檔引用，並通過 `workspace_doctor` |
| `workspace/indicators/` | 用戶自訂指標或計算工具 | extension 有 `manifest.json` 和 `indicator.py`，並通過 `indicator_doctor` |
| `workspace/strategies/` | 可重用策略草稿或進階合約 | AI 或維護者可把它轉成 `runs/` 設定檔 |
| `workspace/runs/` | 可執行回測設定檔 | Run Center 主要讀取這裡的 `strategy_run` JSON |
| `workspace/wfa/` | Walk-forward analysis 設定 | `wfa_run` JSON 需要指向一份 `workspace/runs/...json` |
| `workspace/reports/` | AI 工作紀錄、review、驗收筆記 | 只作紀錄，不會改變回測結果 |

### 外部資料的時間安全

如果策略使用外部資料，例如 IPO 日期、財報、指數成份、情緒指標或你自己的 CSV，必須寫清楚這份資料在現實中何時才知道。這是為了避免回測偷看未來。

如果策略使用 `workspace/features/`，每個 feature 都必須寫 `data_availability`：

- `observed_at`：資料何時被觀察到，例如 `bar_open`、`bar_close`、`after_bar_close`。
- `usable_from`：最早可以在模擬交易中使用的時間，例如 `next_bar_open`。
- `point_in_time`：資料來源是否保留當時已知版本。
- `revision_policy`：資料是 point-in-time、宣告為靜態，還是會修訂歷史。

例子：收盤後才知道的資料，不能用來做同一根 K 線開盤交易。`workspace_doctor` 會阻止這類明顯偷看未來資料的錯誤。

如果 `revision_policy` 是 `revised_history`，代表歷史資料可能曾被修訂。這類資料可以用作研究示範，但不能說成已證明逐時點無偏誤；公開說明或正式結論前必須交量化審查。

### 一個策略常見檔案

同一個策略建議使用相近 slug，方便人和 AI 看出它們相關：

- `datasets/ipo-calendar.csv`
- `features/feature-contract-ipo-breakout-v1.user.json`
- `indicators/extensions/ipo_breakout/`
- `strategies/ipo-breakout.user.json`
- `runs/ipo-breakout-backtest.json`
- `wfa/ipo-breakout-wfa.json`

### AI 應該怎樣處理新檔案

AI 需要先判斷：

- 這是原始資料、特徵合約、自訂指標、可執行回測設定，還是 WFA 設定？
- 日期、代號、時區、交易日曆是否清楚？
- 訊號何時可見？何時才可用於交易？
- 引擎是否已支援這個行為？

如果合約不足，AI 應停止並回報缺少甚麼，不應自行發明可執行設定。

### 驗證指令

建立或修改策略後，先跑：

```powershell
python scripts/workspace_doctor.py --config workspace/runs/your-config.json
```

掃描整個 workspace：

```powershell
python scripts/workspace_doctor.py
```

檢查自訂指標：

```powershell
python scripts/indicator_doctor.py workspace/indicators/extensions
```

### 分享策略或結果

分享策略回測時，可提供 `workspace/runs/...json` 及其引用的 `workspace/datasets/`、`workspace/features/`、`workspace/indicators/` 或 `workspace/wfa/` 檔案，或者叫 AI 複製並打包對應檔案。

其他用戶下載並放到相同位置後，可指派 AI 先跑 `python scripts/workspace_doctor.py`，再用 Run Center 執行。

留意：`workspace/runs/`、`workspace/datasets/`、`workspace/features/` 和 `workspace/wfa/` 只要引用完整、格式正確，通常可以直接分享重跑。`workspace/indicators/extensions/` 是自訂程式碼，不會被自動信任；它需要 `manifest.json`、`indicator.py`、`indicator_doctor` 檢查，以及引擎已有對應調動能力。若檢查通過但引擎仍未支援，AI 必須回報缺少新功能，不可用人造價格曲線、檔名推斷或臨時核心改動假裝可跑。

分享已回測結果時，打包同一個 `run_id` 的本機執行結果包，或者叫 AI 複製並打包對應檔案。常見位置：

- `outputs/app/run_registry/{run_id}.json`
- `outputs/app/chart_payloads/{run_id}/`
- `outputs/app/artifact_manifests/{run_id}.json`
- `outputs/app/run_snapshots/{run_id}/`
- `outputs/app/ai_review/{run_id}/ai_review_pack.json`

`outputs/` 是本機執行結果，預設不應放上公開 GitHub。

## English

`workspace/` is the safe user and AI research area. Normal strategy work should place local data, runnable configs, WFA configs, custom indicators, and AI notes here.

This folder is the only user input entrypoint.

Do not manually edit core folders such as `app/`, `backtester/`, `dataloader/`, `autorunner/`, `wfanalyser/`, `metricstracker/`, or `plotter/` unless you are intentionally making an engine change.

Public GitHub releases keep runnable source examples under `backtester/contracts/strategy/examples/`. On first app launch, lo2cin4bt copies examples into local workspace folders. Run Center also lists any JSON files you add locally. For WFA, do not use a bare filename; use an explicit path such as `workspace/runs/my-strategy.json`. Local research JSON files are ignored by Git by default.

### This Is Not A Magic Inbox

AI must not assume that any CSV or JSON file is usable. A file is used only when it matches a known contract or is explicitly referenced by a `workspace/runs/` config.

### Folder Map

| Folder | Purpose | When It Is Used |
| --- | --- | --- |
| `workspace/datasets/` | Your CSV, Excel, or Parquet data | Referenced by a run config or feature contract |
| `workspace/features/` | Contracts for safely joining external data to price data | Referenced by a run config and validated by `workspace_doctor` |
| `workspace/indicators/` | User custom indicators or calculation tools | Extension has `manifest.json` and `indicator.py`, then passes `indicator_doctor` |
| `workspace/strategies/` | Reusable strategy drafts or advanced contracts | AI or maintainers may convert them into `runs/` configs |
| `workspace/runs/` | Runnable backtest configs | Run Center mainly reads `strategy_run` JSON here |
| `workspace/wfa/` | Walk-forward analysis configs | `wfa_run` JSON points to a `workspace/runs/...json` file |
| `workspace/reports/` | AI notes, reviews, and acceptance records | Records only; does not change engine behavior |

Recommended custom indicator package shape:

- `workspace/indicators/extensions/<package>/manifest.json`
- `workspace/indicators/extensions/<package>/indicator.py`

### External Data Time Safety

If a strategy uses external data, such as IPO dates, earnings releases, index membership, sentiment data, or your own CSV files, it must state when that data would have been known in real life. This prevents the backtest from accidentally looking into the future.

If a strategy uses `workspace/features/`, each feature must declare `data_availability`:

- `observed_at`: when the value first becomes observable, such as `bar_open`, `bar_close`, or `after_bar_close`.
- `usable_from`: earliest simulated trading time allowed to use it, such as `next_bar_open`.
- `point_in_time`: whether the source preserves the value known at that time.
- `revision_policy`: whether the data is point-in-time, declared static, or revised history.

For example, a value known only after the close cannot be used for the same bar open. `workspace_doctor` rejects obvious look-forward timing errors.

If `revision_policy` is `revised_history`, the historical values may have been revised. Such data can be used for research demos, but it is not proof of point-in-time, bias-free availability; public claims or final conclusions require QuantReview.

### Validation Commands

```powershell
python scripts/workspace_doctor.py --config workspace/runs/your-config.json
python scripts/workspace_doctor.py
python scripts/indicator_doctor.py workspace/indicators/extensions
```

### Sharing Strategies Or Results

To share a rerunnable strategy, provide the `workspace/runs/...json` file and every referenced dataset, feature contract, indicator extension, or WFA config.

`workspace/runs/`, `workspace/datasets/`, `workspace/features/`, and `workspace/wfa/` are usually shareable when references are complete and validation passes. `workspace/indicators/extensions/` contains custom code, so it is not trusted automatically. It needs `manifest.json`, `indicator.py`, `indicator_doctor`, and matching runtime support. If the doctor passes but runtime support is missing, AI must report the missing capability instead of pretending the strategy can run.

To share an already completed result, package the same `run_id` under `outputs/app/`. Runtime outputs are local by default and should not be committed to public GitHub.
