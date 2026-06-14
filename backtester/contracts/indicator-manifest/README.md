# Indicator Manifest v1

This folder is the canonical contract root for indicator manifest metadata.

## Purpose
- describe indicator family metadata
- declare backend binding without editing core dispatcher code
- keep extension indicators sharable and auditable

## Core vs Extension
- `manifests/core/*.json`: built-in indicator families shipped with lo2cin4bt
- `workspace/indicators/extensions/<package>/manifest.json`: user extension packages

## Recommended Extension Package Shape
```text
workspace/
  indicators/
    extensions/
      dual_threshold/
        manifest.json
        indicator.py
```

## Multi-Column Readiness
Use `input_contract.mode = "column_params"` when an indicator needs more than one column.

Example:
- `primary_column`
- `confirm_column`

This keeps the system ready for future cases like:
- A value in one condition AND B value in another condition
- cross-dataset confirmations
- richer custom factor logic

The current runtime already allows extension indicators to read arbitrary columns from the input dataframe through their own params.

Important:
- extension indicators should preferably describe conditions or confirmations
- entry and exit decisions should remain in strategy semantics whenever possible
