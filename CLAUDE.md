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

- Gmail 在帳號或應用程式密碼錯誤時會**直接斷線**（SMTPServerDisconnected），不會回「密碼錯誤」。先跑 `trader/mailtest.py`。
- shioaji 1.7 的 `login()` 沒有 `contracts_timeout`；常數改放在 `sj.Action` 等（舊的 `shioaji.constant` 會出 DeprecationWarning）。
- Cowork 連到 Mac 的環境不能刪檔案、不能上網；`tar` 解壓覆蓋既有檔案會失敗，要用 Python 讀寫檔案。

## 目前進度（做完請更新這一段）

最後更新：2026-11-05（Cowork）

- ✔ 網站：中文公司名、三種主題、K 線（含法人副圖）、籌碼、分析師預估、資料品質檢查、盤中提醒、🔔 自訂價位。
- ✔ 永豐 API 測試通過（證券帳戶）。
- ✔ 階段一：每天同步＋對帳信＋私人頁面，launchd 已安裝，寄信已確認成功。
- ◐ 階段二：模擬下單程式完成；11/05 收盤後送出 9 筆（PreSubmitted）。**待確認**：盤中零股在模擬環境是否會成交（11/06 盤中先 `sim_order.py --check`，不要直接重送）。
- 待辦：GitHub Actions →「除錯」mode=probe，確認證交所／櫃買／Yahoo／Google 新聞來源；回測重跑後重新分析；決定真錢怎麼開始投入；階段三（真單、半自動、每筆使用者確認、有勾交易的 Key 要設 IP 限制）。
