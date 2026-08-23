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


def summary(closed: pd.DataFrame, orphans: pd.DataFrame) -> dict:
    """Win rate and average win/loss over the closed lots.

    A win rate is the cheapest honest read on a book, and the two averages
    beside it are what make it meaningful: 15% wins at a 6x payoff and 60%
    wins at 0.4x are different businesses, and the rate alone can't tell them
    apart.

    **Two reasons this is not lifetime performance, and both matter:**

      * The API retains ~3 years. Anything older is simply absent.
      * Sells whose buy predates the window are excluded, not counted as
        zero-basis wins. `orphans` carries them; their proceeds are known and
        their P&L is not. Folding them in would inflate the win rate with
        trades whose cost nobody knows.
    """
    if not len(closed):
        return {}
    wins = closed[closed["gain"] > 0]["gain"]
    losses = closed[closed["gain"] < 0]["gain"]
    return {
        "lots": len(closed),
        "net": closed["gain"].sum(),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": len(wins) / len(closed) * 100,
        "avg_win": wins.mean() if len(wins) else 0.0,
        "avg_loss": losses.mean() if len(losses) else 0.0,
        "payoff": (wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None,
        "avg_hold_days": closed["hold_days"].mean(),
        "excluded_orphan_sells": len(orphans),
    }


if __name__ == "__main__":
    pd.set_option("display.width", 160)
    closed, orphans = realized()
    closed = closed.sort_values("gain")

    st = summary(closed, orphans)
    print(f"=== {st['lots']} closed lots | net realized: ${st['net']:,.0f} ===")
    print(f"  win rate      {st['win_rate_pct']:.1f}%  "
          f"({st['wins']}W / {st['losses']}L)")
    print(f"  avg win       ${st['avg_win']:,.0f}")
    print(f"  avg loss      ${st['avg_loss']:,.0f}"
          + (f"   payoff {st['payoff']:.2f}x" if st["payoff"] else ""))
    print(f"  avg hold      {st['avg_hold_days']:.0f} days")
    print(f"  NOT lifetime  ~3y API retention; "
          f"{st['excluded_orphan_sells']} pre-window sell(s) excluded, not "
          f"counted as zero-basis wins\n")

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
