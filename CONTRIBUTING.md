# Contributing / 參與貢獻

Thanks for helping improve lo2cin4bt. Keep changes small, reviewable, and
educational.

感謝你協助改進 lo2cin4bt。請盡量保持改動細小、容易 review，並保留教學價值。

## Safe Contribution Rules / 安全貢獻規則

- Use `workspace/` for beginner/user inputs, local examples, and user-provided
  files.
- Do not add live trading, broker order placement, fund movement, position
  changes, or account setting changes.
- Broker and exchange code must stay read-only market-data/API oriented unless a
  future explicit contract says otherwise.
- Do not add private local paths, account files, tokens, logs, or personal data.
- Do not claim profitability, future performance, or live-trading readiness from
  demo screenshots or synthetic fixtures.

- 新手輸入、本機範例和用戶提供的檔案應放在 `workspace/`。
- 不要加入實盤交易、券商下單、資金移動、持倉變更或帳戶設定變更。
- 除非未來有明確合約，券商和交易所代碼必須保持只讀行情資料 / API 用途。
- 不要加入私人本機路徑、帳戶檔案、token、logs 或個人資料。
- 不要用示範截圖或 synthetic fixtures 聲稱盈利、未來表現或實盤交易準備完成。

## Before Opening A Pull Request / 開 Pull Request 前

Run the public release defense audit:

先執行公開面防線檢查：

```bash
python scripts/public_release_audit.py
```

For normal code changes, also run the relevant local gates from
`docs/QUALITY_GATES.md`. For strategy, data, WFA, cost/slippage, survivorship,
universe provenance, look-ahead, or result-interpretation changes, read
`docs/QUANT_VALIDATION_GATES.md` first.

一般代碼改動也應按 `docs/QUALITY_GATES.md` 執行相關本機檢查。若改動涉及策略、資料、
WFA、成本 / 滑價、survivorship、universe provenance、look-ahead 或結果解讀，請先閱讀
`docs/QUANT_VALIDATION_GATES.md`。

## Code Cleanup / 代碼清理

Static tools are clues, not automatic deletion permission. Before removing code
because Ruff, Vulture, Deptry, Knip, or coverage reported it, add focused tests
or document why the path is truly unused.

靜態工具只提供線索，不代表可以自動刪代碼。若 Ruff、Vulture、Deptry、Knip 或 coverage
指出某段代碼可疑，刪除前請加入聚焦測試，或清楚記錄為何該路徑確實無用。
