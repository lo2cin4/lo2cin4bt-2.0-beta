# Install And First Run

lo2cin4bt is designed to run locally without Docker. New users need:

- Python 3.12 or newer
- Node.js 20 LTS or newer
- Git
- An AI coding assistant is recommended, either in an IDE or CLI

## Windows Quick Start

```powershell
git clone <repository-url> lo2cin4bt
cd lo2cin4bt
.\scripts\setup.ps1
.\.venv\Scripts\python.exe main.py
```

Then open:

```text
http://127.0.0.1:2424/
```

If port `2424` is already in use, start another local-only port:

```powershell
.\.venv\Scripts\python.exe main.py --port 2425 --no-browser
```

On first app launch, Run Center seeds included examples into `workspace/runs/`.
If you ever need to recreate the BTCUSDT example manually:

```powershell
New-Item -ItemType Directory -Force workspace\runs
Copy-Item backtester\contracts\strategy\examples\strategy-run-btcusdt-binance-daily-dual-ma-example.json workspace\runs\strategy-run-btcusdt-binance-daily-dual-ma-example.json
```

## macOS / Linux Quick Start

```bash
git clone <repository-url> lo2cin4bt
cd lo2cin4bt
bash scripts/setup.sh
.venv/bin/python main.py
```

If port `2424` is already in use:

```bash
.venv/bin/python main.py --port 2425 --no-browser
```

On first app launch, Run Center seeds included examples into `workspace/runs/`.
If you ever need to recreate the BTCUSDT example manually:

```bash
mkdir -p workspace/runs
cp backtester/contracts/strategy/examples/strategy-run-btcusdt-binance-daily-dual-ma-example.json workspace/runs/strategy-run-btcusdt-binance-daily-dual-ma-example.json
```

## Manual Install

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

## Dependency Profiles

- `requirements.txt`: default Python runtime for `python main.py`.
- `requirements-dev.txt`: runtime plus test/dev tools. CI installs this file.
- `requirements-brokers.txt`: runtime plus optional FUTU / IBKR adapter
  packages. Install it only when using `provider=futu` or `provider=ibkr`.
- `plotter/web/package-lock.json`: JavaScript dependency lockfile for the
  React/Vite frontend.

The setup scripts install `requirements.txt` by default. For dev tools, run
`.\scripts\setup.ps1 -Dev` or `bash scripts/setup.sh --dev`. Broker packages are
not part of the first run. Only add `-Brokers` or `--brokers` for optional
market-data gateway experiments; this app does not place live orders.

The setup scripts also run `npm ci` and `npm run build` in `plotter/web` by
default. That downloads Node frontend dependencies from `package-lock.json` and
builds the local React app. The lockfile pins versions and integrity hashes, but
npm packages can still run install scripts. If you want to inspect Node
dependencies first, use `.\scripts\setup.ps1 -SkipFrontend` or
`bash scripts/setup.sh --skip-frontend`; the browser app will need a later
manual `npm ci && npm run build` before `python main.py` can show the frontend.

On first app launch, lo2cin4bt copies included examples from
`backtester/contracts/strategy/examples/` into ignored local folders under
`workspace/runs/` and `workspace/wfa/`. This keeps GitHub clean while making Run
Center usable immediately after setup. WFA configs should reference executable
strategy configs with explicit repo-relative paths such as
`workspace/runs/my-strategy.json`, not bare filenames.

## Development Install

```bash
python -m pip install -r requirements-dev.txt
```

Optional FUTU / IBKR gateway packages:

```bash
python -m pip install -r requirements-brokers.txt
```

FUTU and IBKR also require local gateway applications, account login, API
permissions, and market-data entitlements. These are not solved by Python
dependencies alone. Account or gateway setup is allowed for read-only market
data, but do not enable live trading, enable order placement, move funds, change
positions, change account settings, or treat gateway setup as a release
requirement for local backtesting.

Optional FUTU market-data account note:

- Official redeem page: <https://redeem.futunn.com/redeem>.
- If you independently need a FUTU market-data account, the app flow is:
  download Futubull, register or log in, tap Discover, Me, Event Center,
  Redeem Center, then enter `AZ57KU`.
- In lo2cin4bt this is only for read-only market data. Do not enable trading,
  order placement, fund movement, position changes, or account-setting changes.

## AI Assistant Setup

Ask your AI CLI or IDE assistant to read these files first:

1. `AGENTS.md`
2. `README.md`
3. `README.en.md`
4. `skills/lo2cin4bt/SKILL.md`
5. `skills/lo2cin4bt/references/first-run.md`

Advanced references after the first run:

1. `docs/ai/AI_MANUAL_SKILL.md`
2. `docs/ai/AI_SKILL_LECTURE_GUIDE.md`
3. `workspace/README.md`

A good first prompt is:

```text
Use $lo2cin4bt. Read AGENTS.md, README.md, README.en.md,
skills/lo2cin4bt/SKILL.md, docs/ai/AI_MANUAL_SKILL.md,
docs/ai/AI_SKILL_LECTURE_GUIDE.md, skills/lo2cin4bt/agents/openai.yaml,
and skills/lo2cin4bt/references/first-run.md.
Help me run the simplest local backtest available in workspace/runs, then
explain the output using only repo files and generated artifacts. If workspace
configs are missing, create one supported beginner config first and tell me
where you saved it.
```

Expected first-run evidence:

- `http://127.0.0.1:2424/api/app/health` returns `{"status":"ok"}`.
- Run Center opens in the browser.
- One Backtest run completes, or Codex clearly explains that no local config is
  present and creates/imports one.
- Metrics Overview shows at least one strategy table row.
- Local runtime output appears under `outputs/app/`.

## Advanced AI Assistant References

For deeper automation or maintenance work, ask the assistant to also read:

1. `docs/ai/AI_MANUAL_SKILL.md`
2. `docs/ai/AI_SKILL_LECTURE_GUIDE.md`
3. `workspace/README.md`

## Troubleshooting

Run:

```bash
python scripts/doctor.py
```

If the frontend is missing, run:

```bash
cd plotter/web
npm ci
npm run build
```

If Python dependencies fail to install, verify that your active Python is 3.12+
and that the virtual environment is active. For current troubleshooting, use
`skills/lo2cin4bt/references/troubleshooting.md`.
