# Backtest Validation Status

## Status Summary
Validation program for lo2cin4bt 2.0 has completed major parity rounds.
Primary validated scope:
- dataloader
- statanalyser
- backtester (MA/BOLL/HL/PERC/VALUE, multi-condition, short mirror)
- NDAY1 sequential behavior
- metricstracker
- WFA (standard + anchored + total_config)

## What Is Considered Stable
- NodeIR/native semantic output contract compatibility
- unified portfolio accounting contract and routing behavior
- NDAY exit-only enforcement and post-fill counting rule

## Evidence Location
Public release evidence is kept in the current tests and stable verification
fixtures:

- `tests/`
- `verification/fixtures/`
- `verification/scripts/`

Historical archive notes are not part of the public 2.0.0 source tree.

## Current Priority
After parity completion, priority has shifted to:
- semantic strategy contract rollout
- multi-parameter exploration path
- next stateful strategy family on top of NDAY baseline
