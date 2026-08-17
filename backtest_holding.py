"""Backtest the 'never capitulate' rule: for every lot you sold AT A LOSS,
compare the realized loss to what the position would be worth if held to today
(and to its 52-week high = the best 'sell into strength' exit).

Counterfactual is per-trade and ignores capital redeployment, dividends, and
taxes -- it isolates one question: did cutting losers on price cost or save money?
"""
from __future__ import annotations

import pandas as pd

import etrade
from realized_pnl import realized


def quotes(symbols: list[str]) -> dict:
    out = {}
    # E*TRADE allows batches; keep it simple with chunks of 25.
    for i in range(0, len(symbols), 25):
        chunk = ",".join(symbols[i:i + 25])
        data = etrade.get(f"/v1/market/quote/{chunk}")
        for q in data.get("QuoteResponse", {}).get("QuoteData", []):
            sym = q.get("Product", {}).get("symbol") or q.get("symbol")
            allq = q.get("All", {})
            out[sym] = {"last": allq.get("lastTrade"), "hi52": allq.get("high52")}
    return out


def main():
    closed, _ = realized()
    losers = closed[closed["gain"] < 0].copy()

    # Aggregate each losing name: qty sold, avg sell price, total realized loss.
    agg = (losers.groupby("symbol")
           .apply(lambda d: pd.Series({
               "qty": d["qty"].sum(),
               "avg_sell": (d["sell_price"] * d["qty"]).sum() / d["qty"].sum(),
               "realized_loss": d["gain"].sum(),
           }), include_groups=False)
           .reset_index())

    q = quotes(agg["symbol"].tolist())
    agg["last"] = agg["symbol"].map(lambda s: q.get(s, {}).get("last"))
    agg["hi52"] = agg["symbol"].map(lambda s: q.get(s, {}).get("hi52"))

    # Counterfactuals vs the actual exit.
    agg["hold_to_today"] = (agg["last"] - agg["avg_sell"]) * agg["qty"]
    agg["hold_to_52wk_hi"] = (agg["hi52"] - agg["avg_sell"]) * agg["qty"]

    pd.set_option("display.width", 170)
    cols = ["symbol", "qty", "avg_sell", "last", "hi52",
            "realized_loss", "hold_to_today", "hold_to_52wk_hi"]
    print(agg[cols].round(0).sort_values("hold_to_today").to_string(index=False))

    print(f"\nActual realized loss on these cuts: ${agg['realized_loss'].sum():,.0f}")
    print(f"If instead HELD to today:           "
          f"${agg['hold_to_today'].sum():,.0f}  (vs the actual exit)")
    print(f"If sold into each 52-wk HIGH:        "
          f"${agg['hold_to_52wk_hi'].sum():,.0f}  (vs the actual exit)")
    swing = agg["hold_to_today"].sum() - agg["realized_loss"].sum()
    print(f"\nSwing from not capitulating (hold-to-today vs actual): ${swing:,.0f}")


if __name__ == "__main__":
    main()
