# Indicators / 自訂指標

`indicators/` stores custom indicator extension packages. An indicator is a tool that calculates a signal or a new column from data.

`indicators/` 用來放自訂指標套件。指標是一個工具，負責把數據計算成訊號或新欄位。

## When To Add / 何時需要新增

Add an indicator when existing built-in indicators cannot express your calculation.

當內置指標無法表達你的計算方法時，就需要新增 indicator。

Examples / 例子:

- IPO breakout signal
- MMFI threshold trigger
- VIX plus price confirmation
- custom breadth indicator
- special entry confirmation rule

- IPO 突破新高訊號
- MMFI 閾值觸發
- VIX 加價格雙重確認
- 自訂市場廣度指標
- 特別入場確認規則

## Difference From Features / 和 Features 的分別

`features/` describes data. `indicators/` calculates with data.

`features/` 描述資料；`indicators/` 用資料做計算。

## Package Shape / 套件形狀

Recommended shape:

建議形狀：

- `extensions/<package>/manifest.json`
- `extensions/<package>/indicator.py`

`manifest.json` tells the engine what the indicator is. `indicator.py` contains the actual calculation code.

`manifest.json` 告訴引擎這個指標是甚麼；`indicator.py` 放真正計算邏輯。

## Validation / 驗證

Custom indicators are code. They are not trusted just because they exist under `workspace/`.

自訂指標是程式碼，不會因為放在 `workspace/` 內就自動被信任。

Before a strategy can use one:

使用前必須確認：

- the package has `manifest.json` and `indicator.py`;
- `python scripts/indicator_doctor.py workspace/indicators/extensions` passes;
- the backtest runtime knows how to dispatch that indicator.

- 套件有 `manifest.json` 和 `indicator.py`；
- `python scripts/indicator_doctor.py workspace/indicators/extensions` 通過；
- 回測引擎已有對應的調動能力。

If the doctor passes but runtime support is missing, AI must report the missing capability instead of creating a fake runnable config.

如果檢查通過但引擎仍未支援，AI 必須回報缺少功能，不可以假裝策略可跑。

## Not Full Strategy / 不是完整策略

An indicator should not decide the whole portfolio by itself. The strategy or run config decides how to use the signal.

indicator 不應該自己決定整個投資組合；strategy 或 run config 才決定如何使用訊號。
