# 給 Claude 的專案說明（Claude Code／Cowork 共用）

> 這份檔案是兩邊交接用的：Claude App 的 Code 分頁、Cowork 都會讀同一個資料夾。
> **做完一段工作，請更新最下面的「目前進度」**，另一邊才知道做到哪裡。

## 使用者與溝通方式

- 一律用**繁體中文**回答。使用者主要用手機看網站和信，程式基礎是初學者。
- 開始做事前，先問足夠的釐清問題（可以要求提供資料）；很簡單的問題可以直接答，再問要不要深入。
- 每個步驟附一句「為什麼」。指令要能直接複製貼上，一次一行。
- 不是投資建議：系統是研究工具，Claude 不是持牌顧問。

## 專案是什麼

價值＋成長選股系統（台股、美股、ETF），GitHub repo `KalebChen0509/stock-screener`（**公開**），
網站 https://kalebchen0509.github.io/stock-screener/ 。

| 路徑 | 內容 |
|---|---|
| `config/strategy.yaml` | 篩選條件、排名權重、配置、停損、每日例行（routine） |
| `config/alerts.yaml`、`config/alert_levels.yaml` | 盤中提醒規則；後者由網站「🔔 提醒」產生（JSON 格式的 YAML） |
| `config/positions.yaml` | 已清空：國泰的持股不納入，實際持股改由 Mac 從永豐讀 |
| `screener/` | 資料抓取、指標、篩選、估值、回測、K 線、籌碼、分析師預估、資料品質、通知 |
| `scripts/` | GitHub Actions 的進入點（update_daily、fetch_*、run_backtest、alerts、probe_sources） |
| `site/` | 網站前端（app.js、kchart.js、style.css；三種主題；Lightweight Charts 放在 site/vendor） |
| `trader/` | **永豐 API，只在使用者的 Mac 上執行**（見下方） |
| `tests/` | `python -m tests.test_xxx` 執行，不需 pytest、不需網路 |
| `data` 分支 | Actions 產生的快取，不要手動改 |

GitHub Actions：每日更新（台北週二～週六 06:30）、台股財報回填（每 6 小時）、盤中提醒（每 30 分鐘）、回測與除錯（手動）。

## 怎麼改、怎麼推

- 推上 GitHub 一律用 `bash push_update.sh`：它會先掃描金鑰／身分證字號（有就停止）、合併使用者在 GitHub 網頁改過的設定（pull --rebase）再推。
- `_workflows/` 資料夾：Cowork 不能直接寫 `.github/`，所以把 workflow 檔放 `_workflows/`，`push_update.sh` 會搬過去。Code 分頁可以直接改 `.github/workflows/`。
- 改完先跑相關測試；網站改動要確認手機寬度也正常。
- Mac 上的系統 Python 不一定裝了 requirements；需要時用 `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`（`.venv/` 已排除，不會上傳）。

## 永豐（trader/）— 安全規則，不可違反

- **repo 是公開的**：API Key、Secret、CA 憑證、憑證密碼、身分證字號、Gmail 應用程式密碼、帳號、持股金額，**絕對不能進 repo、不能印在畫面或紀錄檔**。
- 金鑰存在 macOS 鑰匙圈：`sinopac-sync`（正式環境、唯讀：行情／資料＋帳務，**沒有交易權限**）、`sinopac-sim`（模擬環境：行情／資料＋交易，沒有正式環境）、`stock-mail`（寄信：smtp_user／smtp_pass／email_to）。用 `trader/setup_key.py`、`trader/setup_mail.py` 由**使用者自己**輸入，Claude 不輸入、不讀取金鑰內容。
- shioaji 的連線訊息會印出**身分證字號**：所有程式都要先呼叫 `sinopac.silence()`，自己的訊息用 `say()`；紀錄檔寫到 `~/.sinopac/logs/`（`SJ_LOG_PATH`），`*.log` 已排除。帳號只顯示末四碼。
- **真的下單（正式環境）一定由使用者親自確認送出**，Claude 不代替使用者送真單。模擬單（`sinopac-sim`）可以。
- 私人資料只在 `~/.sinopac/`（chmod 700）：`report.html`（完整持股）、`data/`、`sim/`、`us_holdings.yaml`（美股手動記錄）。
- 永豐 API 不能下複委託（美股）的單，也讀不到複委託庫存 → 美股提醒使用者到豐存美股自己下。

| 檔案 | 用途 |
|---|---|
| `trader/sinopac.py` | 共用：鑰匙圈讀取、`silence()`／`say()`、`login(simulation, service)` |
| `trader/sync.py` | 階段一：每天讀永豐庫存，跟網站 orders.json 比對，寄對帳信（內容有變才寄）、產生私人頁面 |
| `trader/install_sync.sh` | 安裝 launchd（`com.stock-screener.sinopac-sync`，每小時檢查、收盤後每交易日一次） |
| `trader/sim_order.py` | 階段二：月下單清單 → 模擬環境（列清單按 y 才送；昨收 +1% 對齊跳動單位；整股＋盤中零股；同月重送要輸入 again；`--check` 查成交） |
| `trader/status.sh` | 檢查所有設定是否完成（只顯示 ✔／✘） |
| `trader/check_account.py`、`api_test.py`、`mailtest.py` | 檢查／測試用 |

執行 trader 程式用 `~/.sinopac-venv/bin/python`（裝了 shioaji 的 venv）。

## 已知的坑

- `silence()` 只擋 stdout；shioaji 連線**出錯**時的訊息走 stderr、也含身分證字號 → 11/06 起 `silence()` 會把 stderr 經過 `redact()` 遮成「A*********」。
  `silence()` 之後 `input()` 的提示文字會消失 → 一律用 `sinopac.ask()`。`api_test.py` 原本沒呼叫 `silence()`（已補）。

- Gmail 在帳號或應用程式密碼錯誤時會**直接斷線**（SMTPServerDisconnected），不會回「密碼錯誤」。先跑 `trader/mailtest.py`。
- shioaji 1.7 的 `login()` 沒有 `contracts_timeout`；常數改放在 `sj.Action` 等（舊的 `shioaji.constant` 會出 DeprecationWarning）。
- Cowork 連到 Mac 的環境不能刪檔案、不能上網；`tar` 解壓覆蓋既有檔案會失敗，要用 Python 讀寫檔案。

## 真錢投入標準（使用者 2026-11-06 事先定好；看到結果後不要改標準）

**使用者：在覺得策略、篩選工具成熟之前都不會投入資金**（沒有時間壓力）。以下是「最低門檻」，達到了也由使用者決定是否成熟。

1. 前瞻模擬（paper，2026-11 開始）**至少 6 個月** → 最早 2027-05 評估。
2. 合格版本要**贏過「只買 ETF」（VT 50＋0050 25＋SPY 25）**：前瞻模擬同期（扣費後）＋10 年回測都要贏。
3. 選用版本的回測最大跌幅要在 **−20% 左右**（使用者能照規則撐住的程度）；超過就降低個股比例（例如「大盤一半＋成長」）。
4. 沒有版本合格 → 就用「只買 ETF」，這也是正當的結果。
5. 進場：**分 6 個月等額分批**。真錢期間組合從高點跌超過 −20% → 暫停加碼、重新檢討（不是自動全賣）。
6. 衍生性商品另外算：槓桿／賣選擇權先回測＋模擬 3 個月，期貨帳戶等回測結果再決定開不開。

## 目前進度（做完請更新這一段）

最後更新：2026-11-05 晚（Code 分頁）

- ✔ 網站：中文公司名、三種主題、K 線（含法人副圖）、籌碼、分析師預估、資料品質檢查、盤中提醒、🔔 自訂價位。
- ✔ 永豐 API 測試通過（證券帳戶）。
- ✔ 階段一：每天同步＋對帳信＋私人頁面，launchd 已安裝，寄信已確認成功。
- ◐ 階段二：模擬下單程式完成；11/05 收盤後送出 9 筆 → 11/06 查：**8 筆盤中零股成交**、3406 失敗（價格超過漲跌幅）。
  原因：盤後送單用了商品檔的參考價（＝11/04 收盤），3406 在 11/05 跌停，限價 949 超過 11/06 漲停 930。
  已修 `sim_order.py`：盤外送單改用快照的最新收盤價、自己算下一盤漲停（`latest_close`、`next_limit_up`）。
  新增 `--only 3406`（只補送清單裡某幾檔；逐檔防重複：先看永豐目前委託狀態，再看本機紀錄）。11/06 補送 3406 @855 已成交。
- ◐ 衍生性商品（11/06 開始）。使用者：目的四種都要（避險、市場訊號、槓桿、賣權）、台灣＋美國、**沒有期貨帳戶、沒有經驗**、資金比例未定、
  槓桿與賣選擇權「先回測＋模擬 3 個月」、開戶等回測結果、資料從 2007 年起。順序：A 資料庫 → B 訊號上網站＋回測 → C 避險回測（跟停損比）→ D 槓桿／賣權只回測＋模擬。
  A 已寫好（未推）：`screener/derivs.py`、`scripts/fetch_derivs.py`、`tests/test_derivs.py`、`.github/workflows/derivs.yml`（回補，push-derivs）、daily.yml 加每日 40 次請求。
  資料：期交所期貨 TX/MTX/TMF、TXO 月選擇權（近 2 個到期月、±10%）、P/C 比（2007 起）；法人期貨未平倉 2018-06 起（期交所只給 3 年，更早用 FinMind）；^VIX、^VIX3M。
  坑：期交所下載一次最多 1 個月、回 HTML 代表查詢錯誤；到期日當天「結算價」欄是 0（已當成缺值，連續報酬改用下個月合約）。
  11/05 15:40 基準：9 筆全是盤中零股、PreSubmitted、成交 0。注意 `--check` 的 list_trades 可能只看得到當天的單。
- ✔ probe（11/05）：證交所、Yahoo、Google 新聞正常；櫃買改了格式（欄位只剩「買進股數／賣出股數／買賣超股數」重複）→ 已修 `screener/chips.py`（照位置對＋英文 OpenAPI），已推。籌碼只用在網站顯示，不影響選股與回測；之前缺的上櫃歷史不會自動補。
- ✔ 回測重跑並分析（11/05，2016-10～2026-09）：目前版（價值）扣費後 CAGR 13.1%，輸給「只買 ETF」15.8% 與混合基準 15.6%；
  弱點是台股個股部位 12.5% vs 0050 24.1%，任何 36 個月都沒贏過。美股個股部位贏 SPY，但平均只持 1.4 檔，且**美股回測沒有下市公司**（Yahoo 沒有）→ 有倖存者偏差，成長動能版（美股 20 檔）受影響最大。
  財報公布時間的處理（台股法定期限、美股實際申報日、月營收用上個月）沒問題；台股有含下市公司。前瞻模擬（paper）2026-11 開始，4 個版本並行。
- ✔ 倖存者偏差評估（11/05，本機實驗，停損只在月底檢查）：台股「含下市 vs 不含下市」只差 ≤1.3%／年且方向不一（價值版不含下市 +0.4%、成長版 −1.3%）。
  美股成長部位（近似 31%，SPY 15%）的超額報酬集中在 305 檔裡的前 10 檔（SMCI、MU、NVDA、FIX、ANET…）；把前 10 檔換成 SPY 報酬 → 18.8%。
  免費的美股下市股價：FinMind 沒有（BBBY 代號被新公司沿用、資料是錯的）、Stooq 有機器人驗證、SEC 索引需要聯絡 email。可行的是 Tiingo 免費 Key（要使用者自己註冊）。
- ◐ 美股下市抽樣（11/06）：使用者已註冊 Tiingo、Key 存在 GitHub Secrets `TIINGO_API_KEY`（Claude 不碰）。
  `screener/us_delisted.py`、`scripts/fetch_us_delisted.py`、`.github/workflows/us_delisted.yml`（每小時 45 檔、自動接續、push-us-delisted）、`tests/test_us_delisted.py`。
  候選 4,111 檔（2016-10～2026-06 停止交易、交易所普通股、排除代號重複／現在被沿用／交易不到一年）→ 固定種子抽 450 檔（weight＝候選數÷450）。
  下一步（資料抓完後）：寫 `scripts/us_survivorship.py`：只用股價的「流動性＋12 個月動能」組合，比較含／不含下市（加權）的報酬差 → 估計偏差。
- ✔ 停損差異查明（11/05）：成長版台股 每日停損 21.5%／月底停損 29.1%／不停損 29.0%（已重現正式數字）。不是程式或資料錯誤（停損前沒有錯價）；
  是 V 型急跌（2018/10、2020/3、2024/8、2025/4）時在低點被洗出場：停損時平均已從高點跌 30%，賣出後 3 個月平均反彈 +10.5%，又被冷卻期擋住不能買回。
  價值版相反：每日停損有幫助（12.5% vs 月底 11.0%）。實際系統（日報）是收盤價停損、隔天賣 → 等於「每日停損」那組。長空頭（2000-02）停損有保護作用。
- ✔ 停損規則比較（11/06，本機回測 10 組）：成長版 每日停損 18.9%／月底檢查 21.0%／放寬（波動×3.5）20.6%／不停損 21.5%；
  冷卻期長短、改看均線買回幾乎沒差（問題在低點賣出，不在冷卻）。前後 5 年都一樣 → 結果穩定。每日停損連最大跌幅都沒有改善。
  價值版相反：現行每日停損 13.1% 最好，月底檢查 12.6%、台股最大跌 −40%。結論：停損規則應該依風格不同。尚未改設定。
  11/06：`risk.stop_loss.check: daily|monthly`（只用在回測；日報尚未支援）＋正式回測加 3 個成長版比較方案（月底檢查／放寬／不停損）。
  坑：月底檢查＋有日線時，移動停損的最高價原本不會在月底更新（只在日線路徑更新）→ 已修，test_synthetic 有回歸測試。
- 待辦：使用者決定下一步（見對話）；決定真錢怎麼開始投入；階段三（真單、半自動、每筆使用者確認、有勾交易的 Key 要設 IP 限制）。
