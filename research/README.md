# 研究程式（不影響網站與每日更新）

用來回答「策略有沒有用、偏差有多大」的一次性分析。結果寫在 `CLAUDE.md` 的「目前進度」，這裡放程式，之後資料更新可以重跑驗證。

## 怎麼跑

資料用 GitHub `data` 分支的快取（從專案根目錄執行）：

```bash
git fetch origin data
```

```bash
mkdir -p /tmp/databr && git archive origin/data | tar -x -C /tmp/databr
```

```bash
SCREENER_CACHE=/tmp/databr/cache .venv/bin/python research/factor_data.py
```

中間結果與輸出放在 `research/out/`（不上傳 GitHub）。

## 程式一覽

| 程式 | 做什麼 | 主要結論（日期） |
|---|---|---|
| `factor_data.py` → `factor_scan.py` | 每月所有股票的指標＋下月報酬 → 單一因子「前 N 檔」組合 vs 0050／SPY | 結構性落後（等權 vs 台積電）；價值／品質／低波動在台股顯著落後；「動能＋財報驚喜」最一致但不顯著（11/06） |
| `stop_var.py` | 停損規則 10 組比較（成長版、價值版） | 成長版每日停損最差、月底檢查≈不停損；價值版每日停損最好（11/06） |
| `hedge_bt.py` | 0050＋避險訊號（2007～2026，含 2008） | 只有趨勢訊號有用（長空頭保險）；放空期貨因逆價差比換現金差；VIX 要用前一天（時差）（11/06） |
| `sleeve_runs.py` → `sleeve_hedge.py` | 選股部位：停損 vs 大盤趨勢避險 | 這 10 年多是 V 型急跌，趨勢避險只降報酬；只在慢空頭有用（11/06） |
| `tw_survivorship.py` | 台股含／不含下市公司 | 偏差 ≤1.3%／年、方向不一（11/05） |
| `us_surv.py` → `us_surv2.py` | 美股抽樣下市股（Tiingo）加權 | 全部可選股沒有偏差；動能越極端偏差越大（前 5% 約 +7%／年，集中在 2016～2021）（11/06，350/450 檔） |
| `chips_factors.py` | 籌碼（法人、融資券）選股與大盤入場條件 | 籌碼不適合選股；入場加外資條件反而更差（11/07） |
| `sahm_tw.py` | 「趨勢＋衰退指標（薩姆、CFNAI、初領）」用在台股 | 沒有用：台股空頭跟科技循環、比美國就業早崩（11/08） |
| `macro_signals.py` | 總經轉折訊號：月底進出場＋大跌前有沒有預警（國發會、OECD、FRED） | 見 CLAUDE.md「總經指標」（11/07）；國發會／OECD 有回溯修正 → 偏樂觀 |

## 已知的坑

- Yahoo 台股日線有錯價（0050 在 2014-01-02 −75%），已用 `screener.prices.repair_tw_jumps` 修正（壓力測試已套用）。
- 美股 VIX 收盤在台北凌晨：台股 t 日只能用美國 t−1 日的值。
- 期交所到期日的結算價欄是 0。
- 美股下市候選裡 1/4 是權證／單位（5 字母 W／U／R 結尾），要排除。
