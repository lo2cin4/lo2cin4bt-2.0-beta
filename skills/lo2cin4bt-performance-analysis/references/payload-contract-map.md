# Payload Contract Map

Use this to connect frontend pages, API endpoints, generated JSON, and source artifacts.

## API Endpoints

| Endpoint | Page | Generated payload |
| --- | --- | --- |
| `GET /api/app/health` | Setup check | none |
| `GET /api/app/command-center` | Command Center | live registry summary |
| `GET /api/app/run-center/configs` | Run Center | workspace config list |
| `POST /api/app/batches` | Run Center | batch id/status |
| `GET /api/app/metrics/runs` | Metrics selector | run registry |
| `GET /api/app/metrics/{run_id}/overview` | Metrics Overview | `outputs/app/chart_payloads/{run_id}/metrics_overview_payload.json` |
| `GET /api/app/metrics/{run_id}/parameter-matrix` | Parameter Matrix | `outputs/app/chart_payloads/{run_id}/parameter_matrix_payload.json` |
| `GET /api/app/backtests/{run_id}/{backtest_id}` | Backtests | `outputs/app/chart_payloads/{run_id}/backtest_detail_{id}.json` or generated on demand |
| `GET /api/app/wfa/{run_id}/dashboard` | WFA | `outputs/app/chart_payloads/{run_id}/wfa_dashboard_payload.json` |
| `GET /api/app/statanalyser/{run_id}` | Factor Analysis | statanalyser payload when generated |
| `GET /api/app/ai-readable/{run_id}` | AI review | `outputs/app/ai_review/{run_id}/ai_review_pack.json` |

## Runtime Output Roots

- `outputs/app/run_registry/`: app run registry.
- `outputs/app/run_snapshots/{run_id}/`: generated snapshots and managed artifacts.
- `outputs/app/artifact_manifests/{run_id}.json`: artifact list and status.
- `outputs/app/chart_payloads/{run_id}/`: frontend JSON payloads.
- `outputs/app/ai_review/{run_id}/`: AI-readable review pack.

These folders are local runtime output and ignored by Git.

## Source Artifact Types

- `metricstracker_parquet`: classic metrics time series.
- `backtester_parquet`: trade/action detail.
- `portfolio_equity_curve_parquet`: portfolio equity through time.
- `portfolio_holdings_parquet`: holdings/selection audit rows.
- `portfolio_rebalance_audit_parquet`: rebalance checkpoints.
- `portfolio_rebalance_trades_parquet`: per-asset rebalance trade rows.
- `portfolio_metadata_json`: config, data health, universe/provenance, summary.
- `wfa_parquet`: WFA/rolling validation selected and diagnostic rows.

## AI Review Procedure

1. Open `ai_review_pack.json`.
2. Confirm run status and artifact manifest readiness.
3. Read `source_payloads` for what the app shows.
4. Read `artifact_table_profiles` for column availability.
5. Use `metric_field_catalog` to discover numeric fields.
6. When a field is absent, report `not generated` or `not available`.
7. Cross-check any surprising UI value against the source artifact profile and payload path.

## Cache Note

The app may cache JSON payloads by schema version. If a payload schema changes, the schema version should bump so stale cached payloads rebuild automatically.
