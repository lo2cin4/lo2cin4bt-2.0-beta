# Security Policy / 安全政策

lo2cin4bt is a local research and education project. No live-trading support is
enabled in this public demo snapshot.

lo2cin4bt 是本機研究與教學專案。此公開示範版本沒有啟用實盤交易功能。

## Supported Scope / 支援回報範圍

Please report security issues related to:

請回報以下安全問題：

- local app behavior
- dependency vulnerabilities
- accidental secret exposure
- unsafe public documentation
- read-only market data connectors
- repository or CI configuration
- 本機應用程式行為
- 依賴套件漏洞
- 意外洩露敏感資訊
- 不安全的公開文件
- 只讀行情資料連接器
- repo 或 CI 設定

Read-only market data means broker or exchange integrations may fetch data, but
must not place orders, move funds, change positions, or change account settings.

只讀行情資料代表券商或交易所整合可以讀取行情，但不得下單、移動資金、改變持倉或修改帳戶設定。

## Out Of Scope / 不屬於安全回報範圍

- Requests to enable live trading
- Profitability claims or strategy performance disputes
- Issues caused by exposing the local app to the public internet
- Private account, broker, or exchange support
- 要求啟用實盤交易
- 盈利聲稱或策略表現爭議
- 將本機應用程式暴露到公開網路後造成的問題
- 私人帳戶、券商或交易所客服問題

## Reporting / 回報方式

Do not open a public issue for secrets, tokens, account details, or exploitable
security reports.

如果問題包含密鑰、token、帳戶資料或可被利用的安全漏洞，請不要開公開 issue。

Send a private report through [Telegram](https://t.me/lo2cin4group) or
[Discord](https://discord.gg/sSnZuq3DNu). Include:

請透過 [Telegram](https://t.me/lo2cin4group) 或
[Discord](https://discord.gg/sSnZuq3DNu) 私下回報，並附上：

- affected version or commit
- steps to reproduce
- expected impact
- whether any secret, token, or personal data is involved
- 受影響版本或 commit
- 重現步驟
- 預期影響
- 是否涉及任何密鑰、token 或個人資料

## Maintainer Response / 維護者回應

The maintainer will triage reports as soon as practical. Critical reports that
affect public users should be handled before new public release work.

維護者會在可行時間內處理回報。若問題嚴重影響公開用戶，應優先於新的公開版本工作處理。
