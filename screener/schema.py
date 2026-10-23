"""標準化的季度財報欄位（台股、美股共用）。

流量欄位（單季值）：revenue, gross_profit, op_income, nonop, pretax, net_income,
                   cfo, capex（正數＝資本支出金額）, cff
存量欄位（季末值）：cash, st_inv, receivables, inventory, current_assets,
                   total_assets, current_liab, total_liab, equity,
                   goodwill, intangibles, lt_inv, debt, shares
"""

FLOW_FIELDS = ["revenue", "gross_profit", "op_income", "nonop", "pretax",
               "net_income", "rd", "cfo", "capex", "cfi", "cff"]
STOCK_FIELDS = ["cash", "st_inv", "receivables", "inventory", "current_assets",
                "total_assets", "current_liab", "total_liab", "equity",
                "goodwill", "intangibles", "lt_inv", "debt", "shares"]
ALL_FIELDS = FLOW_FIELDS + STOCK_FIELDS
# 額外流量欄位（目前只有美股 SEC 有；台股沒有時為空值）：股票酬勞、利息費用、折舊攤銷、發放現金股利、買回庫藏股
EXTRA_FLOW_FIELDS = ["sbc", "interest", "da", "div_paid", "buyback"]
KEY_COLS = ["market", "id", "period_end", "available_date"]
