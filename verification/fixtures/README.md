# Fixtures

這裡放：
- source slice
- 小型手算 / 小型真值資料
- case manifest
- source map

## 子目錄

- `dataloader/`
- `statanalyser/`
- `backtester/`
- `metricstracker/`
- `wfanalyser/`
- `plotter/`
- `manifests/`

## 命名規則

- `*_slice.*`：從現有 import 檔切出的精簡樣本
- `mini_*.*`：為真值驗證專門設計的小型 fixture
- `*_expected.json`：期望值 / spot-check 定義
