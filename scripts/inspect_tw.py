"""除錯用：列出 FinMind 三大報表的原始科目名稱與台積電的標準化結果。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from screener import data_tw  # noqa: E402
from screener.common import load_strategy  # noqa: E402

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 400)


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else "2330"
    fm = data_tw.FinMind(max_calls=10)
    out = []
    raws = {}
    for ds in ("TaiwanStockFinancialStatements", "TaiwanStockBalanceSheet", "TaiwanStockCashFlowsStatement"):
        df = fm.get(ds, sid, "2022-01-01")
        raws[ds] = df
        out.append(f"===== {ds} rows={len(df)}")
        if not df.empty:
            last = df[df["date"] == df["date"].max()]
            for r in last.itertuples():
                out.append(f"{r.date} {r.type:70s} {r.value:>20,.0f}  {getattr(r, 'origin_name', '')}")
    std = data_tw.standardize(raws["TaiwanStockFinancialStatements"], raws["TaiwanStockBalanceSheet"],
                              raws["TaiwanStockCashFlowsStatement"], load_strategy()["reporting_lag"]["tw"], sid)
    out.append("===== standardized")
    out.append(std.tail(8).T.to_string())
    text = "\n".join(out)
    print(text)
    os.makedirs("debug", exist_ok=True)
    with open(f"debug/inspect_tw_{sid}.txt", "w") as f:
        f.write(text)


if __name__ == "__main__":
    main()
