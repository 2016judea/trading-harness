#!/usr/bin/env python3
"""Daily price history + seasonality, via api.nasdaq.com.

Replaces the Yahoo chart API (hard 429s since ~2026-08) and Stooq (JS
proof-of-work bot wall). Nasdaq's public quote endpoint needs no key --
just a browser User-Agent -- and serves ~10 years of daily bars.

Two uses: trailing returns over standard windows, and seasonality tested
against actual data rather than eyeballed off a chart.

CLI:
    python price_history.py AAPL         # 10y seasonality + return windows
    python price_history.py SPY 5        # limit to 5 years

Import:
    from price_history import daily_bars, windows, seasonality
"""
from __future__ import annotations

import datetime as dt
import statistics as st
import sys

import requests

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}
_URL = (
    "https://api.nasdaq.com/api/quote/{sym}/historical"
    "?assetclass={cls}&fromdate={start}&todate={end}&limit=99999"
)


def daily_bars(symbol: str, years: int = 10) -> list[tuple[dt.date, float]]:
    """Ascending [(date, close), ...]. Raises on an empty/blocked response.

    Nasdaq keys history by asset class, so try equities then ETFs -- callers
    shouldn't have to know which one a ticker is.
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=int(365.25 * years) + 5)
    rows: list[dict] = []
    for cls in ("stocks", "etf"):
        url = _URL.format(sym=symbol.upper(), cls=cls, start=start, end=end)
        r = requests.get(url, headers=_UA, timeout=40)
        r.raise_for_status()
        table = (r.json().get("data") or {}).get("tradesTable") or {}
        rows = table.get("rows") or []
        if rows:
            break
    if not rows:
        raise RuntimeError(f"no rows for {symbol} -- endpoint may be blocked or symbol is bad")
    out = []
    for row in rows:
        try:
            d = dt.datetime.strptime(row["date"], "%m/%d/%Y").date()
            out.append((d, float(row["close"].replace("$", "").replace(",", ""))))
        except (KeyError, ValueError):
            continue  # skip malformed rows rather than lose the whole series
    out.sort()
    return out


def windows(bars: list[tuple[dt.date, float]]) -> dict[str, float]:
    """Trailing % returns plus position vs the period high."""
    last_date, last_px = bars[-1]
    res: dict[str, float] = {}
    for label, days in [("1w", 7), ("1m", 30), ("3m", 91), ("6m", 182), ("1y", 365)]:
        prior = [b for b in bars if b[0] <= last_date - dt.timedelta(days=days)]
        if prior:
            res[label] = 100 * (last_px / prior[-1][1] - 1)
    ytd = [b for b in bars if b[0] >= dt.date(last_date.year, 1, 1)]
    if ytd:
        res["ytd"] = 100 * (last_px / ytd[0][1] - 1)
    hi_date, hi_px = max(bars, key=lambda b: b[1])
    res["off_high"] = 100 * (last_px / hi_px - 1)
    res["_high_px"], res["_high_date"] = hi_px, hi_date
    return res


def seasonality(bars: list[tuple[dt.date, float]]) -> dict[int, list[float]]:
    """Month -> list of that calendar month's % returns (month-end to month-end)."""
    monthly: dict[tuple[int, int], float] = {}
    for d, px in bars:
        monthly[(d.year, d.month)] = px  # last close of each month wins
    keys = sorted(monthly)
    by_month: dict[int, list[float]] = {}
    for prev, cur in zip(keys, keys[1:]):
        by_month.setdefault(cur[1], []).append(100 * (monthly[cur] / monthly[prev] - 1))
    return by_month


def main() -> None:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()
    years = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    bars = daily_bars(symbol, years)
    print(f"{symbol}: {len(bars)} bars  {bars[0][0]} -> {bars[-1][0]}  last ${bars[-1][1]:,.2f}")

    w = windows(bars)
    print("\nreturns")
    for k in ("1w", "1m", "3m", "6m", "1y", "ytd"):
        if k in w:
            print(f"  {k:<4} {w[k]:+7.1f}%")
    print(f"  off period high ({w['_high_date']}, ${w['_high_px']:,.2f}): {w['off_high']:+.1f}%")

    print(f"\nseasonality ({years}y) -- small n is data-mining, read with care")
    print(f"  {'mo':<4}{'n':>4}{'mean':>9}{'median':>9}{'win%':>7}")
    for m, vals in sorted(seasonality(bars).items()):
        wins = 100 * sum(1 for v in vals if v > 0) / len(vals)
        name = dt.date(2000, m, 1).strftime("%b")
        print(f"  {name:<4}{len(vals):>4}{st.mean(vals):>+9.2f}{st.median(vals):>+9.2f}{wins:>6.0f}%")


if __name__ == "__main__":
    main()
