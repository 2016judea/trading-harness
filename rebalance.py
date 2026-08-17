#!/usr/bin/env python3
"""Turn a target allocation into an order list. Prints only — never places trades.

Reads the positions CSV written by `fetch_portfolio.py` plus a targets file
(see targets.example.json) and reports, per name, the dollar and share delta
needed to reach the target weight.

Weights are a fraction of INVESTABLE capital, which is:

    (market value of current positions) + cash_on_hand - cash_reserve

so a cash buffer you intend to keep is excluded from the denominator rather
than silently diluting every target.

Because ~every rebalance in a taxable account is really a tax decision, each
row is tagged with whether closing it harvests a loss or realizes a gain.
That tag reads unrealized P&L from the CSV; it does not know your holding
period, so it cannot tell short-term from long-term. Check the lot dates
before acting on a gain.

CLI:
    python rebalance.py                          # uses targets.json
    python rebalance.py --targets my.json
    python rebalance.py --dca 10 --start 2026-06-08   # stage the net buys

Import:
    from rebalance import plan
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _load_targets(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text())
    if "targets" not in cfg:
        raise SystemExit(f"{path}: missing required key 'targets'")
    total_pct = sum(cfg["targets"].values())
    if total_pct > 1.0 + 1e-9:
        raise SystemExit(
            f"{path}: targets sum to {total_pct:.1%} of investable capital, which is "
            "over 100%. Weights are fractions (0.15 = 15%), not percentages."
        )
    return cfg


def plan(portfolio_csv: str | Path = "data/portfolio.csv",
         targets_path: str | Path = "targets.json") -> tuple[pd.DataFrame, dict]:
    """Return (order_list, summary). Positive delta_$ = buy, negative = sell."""
    cfg = _load_targets(targets_path)
    targets: dict[str, float] = cfg["targets"]
    prices: dict[str, float] = cfg.get("prices", {})
    cash_on_hand = float(cfg.get("cash_on_hand", 0))
    cash_reserve = float(cfg.get("cash_reserve", 0))

    df = pd.read_csv(portfolio_csv)
    held_value = dict(zip(df.symbol, df.marketValue))
    held_price = dict(zip(df.symbol, df.lastPrice))
    held_gain = dict(zip(df.symbol, df.totalGain))

    total = df["marketValue"].sum() + cash_on_hand
    investable = total - cash_reserve
    if investable <= 0:
        raise SystemExit(
            f"cash_reserve (${cash_reserve:,.0f}) is >= total capital "
            f"(${total:,.0f}) — nothing left to allocate."
        )

    # Anything held but absent from targets is only touched with sell_unlisted.
    names = list(targets)
    if cfg.get("sell_unlisted"):
        names += [s for s in held_value if s not in targets]

    rows = []
    for sym in names:
        tgt_pct = targets.get(sym, 0.0)
        cur = float(held_value.get(sym, 0.0))
        px = prices.get(sym, held_price.get(sym))
        delta = tgt_pct * investable - cur
        gain = held_gain.get(sym)
        rows.append({
            "symbol": sym,
            "cur_$": round(cur),
            "cur_%": round(100 * cur / total, 1) if total else 0.0,
            "tgt_%": round(100 * tgt_pct, 1),
            "delta_$": round(delta),
            "price": px,
            # A target with no current holding and no price override can't be
            # sized in shares. Report the dollars and say so, don't guess.
            "shares": round(delta / px) if px else None,
            "action": "BUY" if delta > 0 else ("SELL" if delta < 0 else "hold"),
            "tax_on_exit": (
                "n/a" if gain is None or cur == 0
                else "harvest loss" if gain < 0 else "realizes gain"
            ),
        })

    out = pd.DataFrame(rows).sort_values("delta_$")
    unpriced = [r["symbol"] for r in rows if r["shares"] is None and r["delta_$"]]
    summary = {
        "total": total,
        "investable": investable,
        "cash_reserve": cash_reserve,
        "proceeds": float(-out.loc[out["delta_$"] < 0, "delta_$"].sum()),
        "net_buys": float(out.loc[out["delta_$"] > 0, "delta_$"].sum()),
        "unpriced": unpriced,
    }
    return out, summary


def trading_days(start: str, n: int, holidays: set[str] | None = None) -> list[pd.Timestamp]:
    """N weekdays from `start`, skipping any date in `holidays` (YYYY-MM-DD)."""
    holidays = holidays or set()
    days, d = [], pd.Timestamp(start)
    while len(days) < n:
        if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in holidays:
            days.append(d)
        d += pd.Timedelta(days=1)
    return days


def dca(orders: pd.DataFrame, n_days: int, start: str,
        holidays: set[str] | None = None) -> pd.DataFrame:
    """Split the BUY side of an order list evenly across n trading days.

    Sells are left alone — they're assumed to clear first and fund the buys.
    High-priced names round to whole shares, so a given day may show 1-2
    shares. Keep the dollar pace, not the exact share count.
    """
    buys = orders[orders["delta_$"] > 0]
    if buys.empty:
        return pd.DataFrame()

    rows = []
    for i, day in enumerate(trading_days(start, n_days, holidays), 1):
        row = {"day": i, "date": day.strftime("%a %Y-%m-%d")}
        for _, b in buys.iterrows():
            per_day = b["delta_$"] / n_days
            row[f"{b['symbol']}_$"] = round(per_day)
            row[f"{b['symbol']}_sh"] = round(per_day / b["price"]) if b["price"] else None
        row["day_total_$"] = round(buys["delta_$"].sum() / n_days)
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Target allocation -> order list. Prints only.")
    ap.add_argument("--portfolio", default="data/portfolio.csv")
    ap.add_argument("--targets", default="targets.json")
    ap.add_argument("--dca", type=int, metavar="N", help="stage net buys over N trading days")
    ap.add_argument("--start", help="first DCA day, YYYY-MM-DD")
    args = ap.parse_args()

    orders, s = plan(args.portfolio, args.targets)
    pd.set_option("display.width", 170)

    print(f"Total capital ${s['total']:,.0f}   reserve ${s['cash_reserve']:,.0f}   "
          f"investable ${s['investable']:,.0f}\n")
    print(orders.to_string(index=False))
    print(f"\nSell proceeds: ${s['proceeds']:,.0f}   net buys: ${s['net_buys']:,.0f}")
    if s["unpriced"]:
        print(f"No price for {', '.join(s['unpriced'])} — not currently held. "
              f"Add a \"prices\" entry to the targets file to size these in shares.")

    if args.dca:
        if not args.start:
            raise SystemExit("--dca requires --start YYYY-MM-DD")
        cfg = _load_targets(args.targets)
        sched = dca(orders, args.dca, args.start, set(cfg.get("holidays", [])))
        if sched.empty:
            print("\nNothing to DCA — no net buys in this plan.")
        else:
            print(f"\n=== DCA: ${s['net_buys']:,.0f} over {args.dca} trading days ===")
            print(sched.to_string(index=False))

    print("\nThis is an order list, not an order. Nothing was placed.")


if __name__ == "__main__":
    main()
