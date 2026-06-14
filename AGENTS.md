# Lo2cin4BT AI Agent Guide / AI 代理指南

Use this file as the first instruction page when an AI CLI or AI IDE assistant
is helping with this repository.

當 AI CLI 或 AI IDE assistant 協助處理此 repo 時，應先閱讀此檔案。

## Ground Rules / 基本規則

- Read `README.md`, `skills/lo2cin4bt/SKILL.md`, and
  `docs/ai/AI_MANUAL_SKILL.md` before creating configs, changing code, or
  explaining app behavior.
- Treat repository files as the source of truth. If you add outside finance,
  quant, or engineering context, label it as external context or AI inference.
- Keep user-editable examples under `workspace/`.
- Do not commit runtime outputs, logs, caches, local `.env` files, broker
  credentials, API keys, or generated verification artifacts.
- New strategy examples should use `strategy_run` unless a test explicitly
  covers compatibility behavior.
- User-facing naming follows `docs/NAMING.md`. Use Historical Universe
  Constituents / 歷史成分股 for date-aware universe constituent tables.

- 建立設定檔、修改代碼或解釋 app 行為前，先閱讀 `README.md`、
  `skills/lo2cin4bt/SKILL.md` 和 `docs/ai/AI_MANUAL_SKILL.md`。
- repo 內檔案是事實來源。若加入外部金融、量化或工程背景，請標明那是外部背景或 AI 推論。
- 用戶可修改的範例應放在 `workspace/`。
- 不要提交執行輸出、logs、cache、本機 `.env`、券商憑證、API keys 或生成的驗證產物。
- 新策略範例應使用 `strategy_run`，除非測試明確覆蓋相容行為。
- 用戶可見命名遵循 `docs/NAMING.md`。具日期意識的成分股表使用 Historical Universe
  Constituents / 歷史成分股。

## Useful Entry Points / 常用入口

- App launcher: `python main.py`
- User configs: `workspace/runs/`, `workspace/wfa/`, `workspace/strategies/`
- Repo-local Codex skill: `skills/lo2cin4bt/SKILL.md`
- AI operation manual: `docs/ai/AI_MANUAL_SKILL.md`
- AI teaching guide: `docs/ai/AI_SKILL_LECTURE_GUIDE.md`
- Install guide: `docs/INSTALL.md`
- Naming notes: `docs/NAMING.md`
- Runtime smoke check: `python scripts/doctor.py`

- 啟動 app：`python main.py`
- 用戶設定：`workspace/runs/`、`workspace/wfa/`、`workspace/strategies/`
- repo-local Codex skill：`skills/lo2cin4bt/SKILL.md`
- AI 操作手冊：`docs/ai/AI_MANUAL_SKILL.md`
- AI 教學指南：`docs/ai/AI_SKILL_LECTURE_GUIDE.md`
- 安裝指南：`docs/INSTALL.md`
- 命名說明：`docs/NAMING.md`
- 執行環境快速檢查：`python scripts/doctor.py`

## Local Output Boundary / 本機輸出邊界

Generated artifacts belong in ignored runtime folders such as `outputs/`,
`logs/`, `plotter/web/dist/`, and `verification/*` output folders. Strategy
research files should normally stay under `workspace/`.

生成產物應放在已忽略的執行資料夾，例如 `outputs/`、`logs/`、`plotter/web/dist/`
和 `verification/*` output folders。策略研究檔案通常應留在 `workspace/` 內。
