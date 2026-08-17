"""Reconstruct realized P&L by FIFO-matching sells against prior buys per symbol.

Each closed lot becomes a row: symbol, buy/sell dates, qty, prices, $ gain, %
return, holding days. Sells with no matching buy inside the API's ~3-year
window are reported separately (their cost basis predates our data).
"""
from __future__ import annotations

from collections import defaultdict, deque

import pandas as pd

from load import trades


def realized() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = trades().sort_values("transactionDate")
    lots = defaultdict(deque)        # symbol -> queue of [qty, price, date]
    closed, orphan_sells = [], []

    for _, r in df.iterrows():
        sym = r["brokerage.displaySymbol"]
        qty = abs(float(r["brokerage.quantity"]))
        price = float(r["brokerage.price"])
        date = r["transactionDate"]
        if r["transactionType"] == "Bought":
            lots[sym].append([qty, price, date])
            continue

        # Sold: match FIFO against open buy lots.
        remaining = qty
        while remaining > 1e-9 and lots[sym]:
            lot = lots[sym][0]
            take = min(remaining, lot[0])
            gain = (price - lot[1]) * take
            closed.append({
                "symbol": sym, "qty": take,
                "buy_date": lot[2], "buy_price": lot[1],
                "sell_date": date, "sell_price": price,
                "gain": gain,
                "return_pct": (price / lot[1] - 1) * 100 if lot[1] else None,
                "hold_days": (date - lot[2]).days,
            })
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-9:
                lots[sym].popleft()
        if remaining > 1e-9:  # sold more than we have a basis for
            orphan_sells.append({
                "symbol": sym, "qty": remaining, "sell_date": date,
                "sell_price": price, "proceeds": remaining * price,
            })

    return pd.DataFrame(closed), pd.DataFrame(orphan_sells)


if __name__ == "__main__":
    pd.set_option("display.width", 160)
    closed, orphans = realized()
    closed = closed.sort_values("gain")

    print(f"=== {len(closed)} closed lots | "
          f"net realized: ${closed['gain'].sum():,.0f} ===\n")

    print("WORST 10 closed trades:")
    print(closed.head(10).to_string(index=False))
    print("\nBEST 10 closed trades:")
    print(closed.tail(10).to_string(index=False))

    print("\n=== realized gain by symbol ===")
    by_sym = (closed.groupby("symbol")
              .agg(lots=("gain", "size"), realized=("gain", "sum"),
                   avg_ret_pct=("return_pct", "mean"),
                   avg_hold_days=("hold_days", "mean"))
              .sort_values("realized"))
    print(by_sym.to_string())

    if len(orphans):
        print(f"\n=== {len(orphans)} sells with pre-window cost basis "
              f"(proceeds ${orphans['proceeds'].sum():,.0f}) ===")
        print(orphans.to_string(index=False))
