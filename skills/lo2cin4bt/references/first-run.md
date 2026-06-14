# First Run For Beginners

Use this when the user is new, has only Codex or an AI coding assistant, and wants to get lo2cin4bt running.

## What The User Needs

- Windows 10/11, macOS, or Linux.
- Python 3.12 or newer.
- Node.js 20 LTS or newer.
- Git, or the ability to download a GitHub ZIP.
- No broker account is required for beginner local backtests. FUTU, IBKR, or exchange accounts may be used later for read-only market data only.

## Download

Preferred:

```bash
git clone <repository-url> lo2cin4bt
cd lo2cin4bt
```

ZIP fallback:

1. Download the repository ZIP from GitHub.
2. Extract it to a simple path such as `D:\lo2cin4bt` or `~/lo2cin4bt`.
3. Open Codex or the terminal with that folder as the working directory.

The public repo does not track local `workspace/runs/*.json` or `workspace/wfa/*.json` files. On first app launch, lo2cin4bt copies included examples into those ignored workspace folders. WFA configs in `workspace/wfa/` must point to strategy configs with explicit repo-relative paths like `workspace/runs/my-strategy.json`; do not write only the filename. If the local BTCUSDT beginner example was deleted, recreate it with:

Windows:

```powershell
New-Item -ItemType Directory -Force workspace\runs
Copy-Item backtester\contracts\strategy\examples\strategy-run-btcusdt-binance-daily-dual-ma-example.json workspace\runs\strategy-run-btcusdt-binance-daily-dual-ma-example.json
```

macOS / Linux:

```bash
mkdir -p workspace/runs
cp backtester/contracts/strategy/examples/strategy-run-btcusdt-binance-daily-dual-ma-example.json workspace/runs/strategy-run-btcusdt-binance-daily-dual-ma-example.json
```

For other examples, use the seeded included examples, add configs from the owner/community channel, or ask Codex to create a supported `strategy_run` config using `feature-recipes.md`.

## Windows Setup

```powershell
cd lo2cin4bt
.\scripts\setup.ps1
.\.venv\Scripts\python.exe main.py
```

Open:

```text
http://127.0.0.1:2424/
```

## macOS / Linux Setup

```bash
cd lo2cin4bt
bash scripts/setup.sh
.venv/bin/python main.py
```

Open:

```text
http://127.0.0.1:2424/
```

## Manual Setup

Use this when setup scripts fail or the user wants to see every step.

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r requirements.txt
cd plotter/web
npm ci
npm run build
cd ../..
python scripts/doctor.py
python main.py
```

## First Successful Run

1. Open Run Center.
2. Pick one Backtest config from `workspace/runs/`.
3. Click the run button for Backtests.
4. Wait until the batch completes.
5. Open Metrics Overview from the result.
6. Confirm at least one row appears in Strategy Table.
7. Open Backtests and inspect equity, drawdown, rebalance/trade rows, costs, and data health.

Good evidence:

- `GET http://127.0.0.1:2424/api/app/health` returns `{"status":"ok"}`.
- Run Center shows the completed run.
- Metrics Overview loads without page error.
- `outputs/app/run_snapshots/{run_id}/` exists locally.
- `outputs/app/ai_review/{run_id}/ai_review_pack.json` exists after payload export.

## First Prompt For Codex

```text
Use $lo2cin4bt. Read AGENTS.md, README.md, README.en.md,
skills/lo2cin4bt/SKILL.md, and skills/lo2cin4bt/references/first-run.md.
Help me run the simplest local backtest available in workspace/runs, then explain
the result using only repo files and generated artifacts. If workspace configs are
missing, create one supported beginner config first and tell me where you saved it.
```

## Common Beginner Choices

- Backtest first, WFA later.
- Use yfinance ETF examples before broker/gateway providers.
- Use one small config before a large Parameter Matrix.
- Keep external broker packages and FUTU/IBKR out of the first run unless the user is explicitly setting up read-only market data.
