# 價值選股系統（台股＋美股＋ETF）

由上而下（產業、大盤）＋由下而上（財報硬性條件）交叉比對的價值型選股網站。
每天美股收盤後由 GitHub Actions 自動更新資料、重新篩選、部署網站，並用 LINE／Email 通知。

## 結構

| 路徑 | 內容 |
|---|---|
| `config/strategy.yaml` | **所有篩選條件、排名權重、配置比例、交易成本**（改這裡就能調整策略） |
| `config/etf_universe.yaml` | ETF 候選池與費用率 |
| `config/watchlist.yaml` | 追蹤清單（每日通知會回報狀態） |
| `screener/` | 資料抓取、指標計算、篩選、排名、回測、通知 |
| `scripts/` | 每日更新、回填、回測的進入點 |
| `site/` | 網站前端（GitHub Pages） |
| `data` 分支 | 自動產生的快取資料（不用手動修改） |

## 自動排程（.github/workflows）

- **每日更新**：台北時間週二至週六 06:30。
- **台股財報回填**：每小時一次，第一次約需 10 小時把全部上市櫃公司 10 年財報抓完，之後只做增量更新。
- **回測**：手動執行（Actions → 回測 → Run workflow）。
- **盤中提醒**：台股、美股盤中每 30 分鐘（停損線、自己設的價位、均線／KD 訊號、合理價）；價位在網站「看盤」頁按「🔔 提醒」設定，存在 `config/alert_levels.yaml`。
- **除錯**：手動執行。`probe` 檢查外部資料來源（籌碼、分析師預估、新聞、盤中報價）還能不能用；`inspect` 列出 FinMind 原始科目名稱。

## 需要設定的 Secrets（Settings → Secrets and variables → Actions）

| 名稱 | 用途 |
|---|---|
| `FINMIND_TOKEN` | 台股財報（finmindtrade.com 免費註冊） |
| `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_USER_ID` | LINE 通知（LINE Messaging API） |
| `SMTP_USER`、`SMTP_PASS`、`EMAIL_TO` | Email 通知（Gmail 請用「應用程式密碼」） |

## 資料來源

- 台股財報、股票清單：FinMind
- 美股財報：SEC EDGAR XBRL（含實際申報日，避免前視偏誤）
- 股價、ETF 資料、匯率：Yahoo Finance
- 台股三大法人、融資融券：臺灣證券交易所、櫃買中心（只用在網站與 K 線副圖）
- 美股分析師預估：Yahoo Finance（只用在網站與財報快報）
- 新聞：FinMind／Yahoo Finance，抓不到時改用 Google 新聞 RSS

## 安全

- 這個 repo 是公開的：**任何金鑰、憑證、身分證字號、實際帳戶資料都不能放進來**。`push_update.sh` 推送前會自動檢查，發現疑似金鑰就停止。
- 之後串接永豐 API 的程式在你的 Mac 上執行，金鑰與憑證放在 `~/.sinopac/`（不在這個資料夾裡）。

## 回測限制

股票池只含目前仍掛牌的公司（倖存者偏差，結果偏樂觀）；ETF 費用率與規模用現值。
本系統僅供研究參考，不構成投資建議。
