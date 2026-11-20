"""The long-bond yield, from the U.S. Treasury's daily par yield curve. No key.

owner_earnings.py capitalises owner earnings at the 30-year Treasury, so the
connector needs that number and the date it was set. Treasury publishes the
whole curve as a CSV per year; the first data row is the latest business day.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import time
import urllib.request

URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
       "daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
       "&field_tdr_date_value={y}&page&_format=csv")
PAGE = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
        "TextView?type=daily_treasury_par_yield_curve")
TTL = 6 * 3600
_memo: dict = {}


class BondUnavailable(RuntimeError):
    pass


def _year(y: int) -> list[dict]:
    req = urllib.request.Request(URL.format(y=y), headers={"User-Agent": "trading-harness"})
    with urllib.request.urlopen(req, timeout=8) as r:
        text = r.read().decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(text)))


def long_bond(tenor: str = "30 Yr") -> dict:
    """{'pct': 5.40, 'date': '2026-11-19', 'tenor': '30 Yr', 'source': ...}"""
    hit = _memo.get(tenor)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    today = dt.date.today()
    last_err = None
    for y in (today.year, today.year - 1):   # first days of January: last year's file
        try:
            for row in _year(y):
                v = (row.get(tenor) or "").strip()
                if v:
                    d = dt.datetime.strptime(row["Date"], "%m/%d/%Y").date().isoformat()
                    out = {"pct": float(v), "date": d, "tenor": tenor,
                           "source": "U.S. Treasury daily par yield curve", "source_url": PAGE}
                    _memo[tenor] = (time.time(), out)
                    return out
        except Exception as e:  # network, format change
            last_err = e
    raise BondUnavailable(f"Treasury yield curve unreachable ({last_err}); "
                          "pass long_bond_pct instead.")
