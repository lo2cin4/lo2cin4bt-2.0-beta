# Workspace Adaptors

Workspace adaptors are small, local conversion steps that turn user files into
the shapes lo2cin4bt already understands.

They should live at the edge of `workspace/`, not inside core engine folders,
unless the adaptor becomes a supported public feature with tests and docs.

## Design Rules

- User input stays under `workspace/`.
- Adaptors must be deterministic and repeatable.
- Adaptors must write generated files to ignored workspace paths.
- Adaptors must validate required columns before producing configs.
- Adaptors must not place secrets, account data, or private local paths in Git.
- Adaptors must not enable live trading, orders, funds, positions, or account
  setting changes.

## IPO CSV Adaptor Example

User file:

```text
workspace/datasets/ipo_calendar.csv
```

Minimum columns:

| Column | Meaning |
| --- | --- |
| `symbol` | Tradable ticker |
| `ipo_date` | Listing date in ISO format, for example `2026-01-15` |
| `source` | Where this IPO calendar row came from |
| `source_asof_date` | Date when this calendar snapshot was known |

Optional columns:

| Column | Meaning |
| --- | --- |
| `exchange` | Exchange or venue label |
| `name` | Company name |
| `currency` | Trading currency |
| `status` | Listed, withdrawn, delisted, renamed, or other source status |
| `previous_symbol` | Previous ticker when a symbol changed |
| `delisting_date` | Date when the symbol stopped trading, if known |

Strategy idea:

1. Do not trade on the first IPO day.
2. After day one, track the post-IPO high.
3. Enter long when price breaks a new post-IPO high.
4. Exit after 30 calendar days or the configured holding window.

Adaptor output should be a local research config under:

```text
workspace/runs/
```

The adaptor should also write a small validation note under:

```text
workspace/reports/agents/
```

## Required Validation

Before creating a runnable config, the adaptor must check:

- no missing `symbol`
- no missing `ipo_date`
- no missing `source`
- no missing `source_asof_date`
- valid date parsing
- no duplicate `(symbol, ipo_date)` rows
- every row has a source snapshot date that is not after the simulated decision
  date
- symbol changes, withdrawn IPOs, and delistings are either represented or
  explicitly reported as unavailable
- the universe is documented as point-in-time or clearly labeled as not
  survivorship-safe
- data source can provide OHLCV after `ipo_date`
- strategy uses only data available after each decision timestamp

## AI Workflow

A beginner can ask:

```text
Read workspace/datasets/ipo_calendar.csv, validate the columns, then create a
local lo2cin4bt strategy config in workspace/runs/ for: first day no trade,
buy when the IPO stock breaks a new post-IPO high, and exit after 30 days. Do
not edit core engine folders unless the current contracts cannot express the
strategy; if contracts are insufficient, stop and report the missing contract.
```

## Contract Boundary

If the existing strategy contract cannot express "post-IPO high breakout" or
"exit after 30 days" without new code, the AI must stop and report the missing
contract instead of inventing hidden behavior.
