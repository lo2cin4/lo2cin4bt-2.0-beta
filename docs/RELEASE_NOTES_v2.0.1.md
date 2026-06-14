# lo2cin4bt 2.0.1 Release Notes

Beta patch release for public testing.

## Fixed

- Fixed blank pages on Strategy Performance, WFA, Parameter Matrix, and
  Backtests routes caused by Plotly React component loading in production
  browser bundles.
- Updated project and API version metadata from `2.0.0` to `2.0.1`.

## Validation

- Frontend tests passed.
- Frontend production build passed.
- Browser route verification confirmed the affected pages render.
- GitHub Actions passed: CI, CodeQL, and Semgrep.

## Safety Notice

lo2cin4bt is for local research, teaching, and backtesting only. It is not
investment advice, not live-trading software, and not an order-routing system.
It must not be used to place orders, move funds, change positions, or change
broker or exchange account settings.
