# Troubleshooting lo2cin4bt

This is the browser-first troubleshooting guide for lo2cin4bt. The app runs
through FastAPI + React at `http://127.0.0.1:2424/`.

這是 lo2cin4bt 的瀏覽器優先排錯指南。應用程式透過 FastAPI + React 在
`http://127.0.0.1:2424/` 本機網址運行。

For AI-assisted recovery, use:

如需 AI 協助排錯，請使用：

```text
skills/lo2cin4bt/references/troubleshooting.md
```

## First Health Check / 第一個健康檢查

```bash
python scripts/doctor.py
```

Expected:

預期結果：

- Python 3.12+.
- Required runtime packages installed.
- Node.js and npm available unless you intentionally skip frontend checks.
- `plotter/web/package-lock.json` exists.
- Python 3.12 或以上。
- 已安裝必要執行套件。
- 除非你刻意跳過前端檢查，否則 Node.js 和 npm 應可使用。
- `plotter/web/package-lock.json` 存在。

## App Does Not Start Or Homepage Shows 503 / 應用程式無法啟動或首頁顯示 503

1. Confirm dependencies are installed.
2. Rebuild frontend if `plotter/web/dist/` is missing.
3. Launch the app again.

1. 確認依賴套件已安裝。
2. 如果缺少 `plotter/web/dist/`，重新建置前端。
3. 再次啟動應用程式。

```bash
cd plotter/web
npm ci
npm run build
cd ../..
python main.py
```

Why this happens: `python main.py` serves the React production build from
`plotter/web/dist/`. A clean GitHub checkout does not include `dist/`, because it
is a generated build artifact. The normal `scripts/setup.ps1` / `scripts/setup.sh`
path creates it automatically unless you use the frontend-skip option.

原因：`python main.py` 會讀取 `plotter/web/dist/` 內的 React 正式建置。乾淨
GitHub checkout 不會包含 `dist/`，因為它是本機生成產物。正常使用
`scripts/setup.ps1` 或 `scripts/setup.sh` 時會自動建立它，除非你選擇跳過前端。

If you built `dist/` while `python main.py` was already running, stop and restart
`python main.py`. The app only mounts `/assets` at startup, so a server that was
started before `plotter/web/dist/assets/` existed can show a blank white page.

如果你在 `python main.py` 已運行時才建置 `dist/`，請停止並重新啟動
`python main.py`。應用程式只會在啟動時掛載 `/assets`，所以太早啟動的伺服器
可能會顯示空白頁。

Open:

打開：

```text
http://127.0.0.1:2424/
```

Check API health:

檢查 API 健康狀態：

```text
http://127.0.0.1:2424/api/app/health
```

## Port 2424 Is Occupied / 2424 連接埠已被佔用

Windows:

```powershell
Get-NetTCPConnection -LocalPort 2424
```

Stop the old local app process only if it belongs to your lo2cin4bt session.

只有在確認舊 process 屬於你的 lo2cin4bt session 時，才停止它。

## Run Center Shows No Configs / 執行中心沒有顯示設定檔

Run Center reads local config folders:

執行中心會讀取以下本機設定資料夾：

- `workspace/runs/`
- `workspace/wfa/`

Public GitHub snapshots may intentionally omit local/user configs. On first app
launch, lo2cin4bt seeds included examples into `workspace/runs/` and
`workspace/wfa/`. If examples are missing, restart the app or ask the PM agent to
run the workspace checks.

公開 GitHub snapshot 可能刻意不包含本機或用戶自己的設定檔。第一次啟動應用程式時，
lo2cin4bt 會把內建範例補到 `workspace/runs/` 和 `workspace/wfa/`。如果範例消失，
請重啟應用程式，或請 PM 代理執行工作區檢查。

## Run Finished But Page Looks Empty / 回測完成但頁面看起來是空的

Inspect:

檢查：

- `outputs/app/artifact_manifests/{run_id}.json`
- `outputs/app/run_snapshots/{run_id}/`
- `outputs/app/chart_payloads/{run_id}/`
- `outputs/app/ai_review/{run_id}/ai_review_pack.json`

If the artifact is from an older version and lacks required fields, rerun the
strategy with the current app instead of treating missing fields as zero.

如果產物來自舊版本而缺少必要欄位，請用目前版本重新回測，不要把缺失欄位當成零。

## Data Provider Fails / 資料來源失敗

- yfinance: check symbol spelling and network access.
- Binance/Coinbase: use provider-specific symbol formats.
- File-backed data: confirm `Time`, `Open`, `High`, `Low`, `Close`, and
  optionally `Volume` are present or mappable.
- FUTU/IBKR: optional market-data gateway work only; it is not part of the
  first run and this app does not place live orders. Missing gateway/account
  permissions are environment issues, not strategy config issues.

- yfinance：檢查代號拼寫和網路連線。
- Binance/Coinbase：使用該資料來源要求的代號格式。
- 本機檔案資料：確認有 `Time`、`Open`、`High`、`Low`、`Close`，以及可選的
  `Volume`，或已清楚映射欄位。
- FUTU/IBKR：只屬於可選的行情資料 gateway，不是第一次使用必須完成的流程；
  lo2cin4bt 目前不會真實下單。缺少 gateway 或帳戶權限屬於環境問題，不是策略設定問題。

## Result Looks Wrong / 結果看起來不合理

Do not debug from a screenshot alone. Check in this order:

不要只靠截圖排錯。請按以下順序檢查：

1. Selected config.
2. Normalized config/snapshot.
3. Provider, calendar, timezone, and benchmark.
4. Data health, effective start, missing assets, and universe provenance.
5. Costs and slippage.
6. Equity, holdings, rebalance audit, and rebalance trades artifacts.
7. Parameter Matrix or WFA payloads only if those workflows were generated.
8. Frontend payload JSON and page component.

1. 目前選取的設定檔。
2. 標準化後的設定檔 / snapshot。
3. 資料來源、交易日曆、時區和基準。
4. 資料健康、有效開始日、缺失資產和 universe provenance。
5. 成本和滑價。
6. 資金曲線、持倉、再平衡 audit 和再平衡交易產物。
7. 只有在生成了 Parameter Matrix 或 WFA 時，才檢查相關 payload。
8. 前端 payload JSON 和頁面 component。

See `skills/lo2cin4bt/references/quant-interpretation-risks.md` for result
interpretation traps.

結果解讀常見陷阱可參考 `skills/lo2cin4bt/references/quant-interpretation-risks.md`。
