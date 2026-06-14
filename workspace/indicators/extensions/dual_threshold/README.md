# dual_threshold / 雙條件確認指標

`dual_threshold` is an example custom indicator extension.

`dual_threshold` 是一個自訂指標示例。

## What It Does / 它做甚麼

It checks two columns. When both conditions become true, it emits an entry confirmation signal.

它會檢查兩個欄位。當兩個條件同時成立時，輸出入場確認訊號。

Example / 例子:

- primary column > primary threshold
- confirm column > confirm threshold

- 主要欄位 > 主要門檻
- 確認欄位 > 確認門檻

## Files / 檔案

- `manifest.json`: describes required parameters and signal meaning
- `indicator.py`: implements the calculation

- `manifest.json`：描述需要甚麼參數和訊號意思
- `indicator.py`：實作計算邏輯

## Beginner Note / 新手提示

This is a tool, not a complete strategy. A run or strategy config still decides which asset to trade, position size, exit rule, cost, and timing.

這是一個工具，不是一個完整策略。真正買賣哪個資產、倉位大小、出場規則、成本和執行時間，仍由 run 或 strategy config 決定。
