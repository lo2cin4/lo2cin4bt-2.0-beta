# Indicator Extensions / 指標擴充套件

This folder contains user-defined indicator packages.

這個資料夾放用戶自訂的 indicator package。

Each package should have its own folder.

每個套件應該有自己的資料夾。

Example / 例子:

- `dual_threshold/`
- `ipo_breakout/`
- `mmfi_threshold/`

Expected files / 預期檔案:

- `manifest.json`: metadata and input/output contract
- `indicator.py`: calculation code

- `manifest.json`：元資料和輸入/輸出合約
- `indicator.py`：計算程式

Keep package names short, lowercase, and related to the strategy slug when possible.

套件名稱建議短、小寫，並盡量和策略短名相關。

Validation / 驗證：

- Run `python scripts/indicator_doctor.py workspace/indicators/extensions`.
- Passing the doctor means the package shape is acceptable.
- A strategy can run only when the backtest runtime also supports dispatching that indicator.
- If runtime support is missing, AI should stop and report the missing capability.

- 執行 `python scripts/indicator_doctor.py workspace/indicators/extensions`。
- 通過 doctor 代表套件形狀合格。
- 策略能否執行，仍取決於回測引擎是否支援調動該指標。
- 如未支援，AI 應停下並回報缺少功能。
