#!/usr/bin/env python3
"""The Claude directory test cases (SUBMISSION.md), run against the live endpoint.

    python3 scripts/live_cases.py [endpoint]

One line per case: PASS, FAIL, or BLOCKED (a valuation case while the server's SEC
contact is unset). Calls carry x-th-test: 1, so analytics marks them as tests.
Prices are fixed test inputs, not quotes. Exit 0 only when every case passes.
Standard library only, so it runs on any python3.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

URL = sys.argv[1] if len(sys.argv) > 1 else "https://trading-harness-mcp.vercel.app/mcp"
ROOT = Path(__file__).resolve().parents[1]
EX = {n: (ROOT / "data" / f"example_{n}.csv").read_text() for n in ("transactions", "portfolio", "orders")}
JOURNAL = (ROOT / "JOURNAL.template.md").read_text()
ETRADE = """For Account:,#####0000

Download Date:,11/20/2026

TransactionDate,TransactionType,SecurityType,Symbol,Quantity,Amount,Price,Commission,Description
11/14/25,Sold,EQ,KO,-100,6984.50,69.85,0.50,COCA COLA CO
10/02/25,Dividend,EQ,KO,0,51.00,0,0,COCA COLA CO
01/06/25,Bought,EQ,KO,100,-6120.00,61.20,0,COCA COLA CO
"""


def rpc(method, params=None):
    req = urllib.request.Request(URL, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                       "params": params or {}}).encode(),
                                 headers={"content-type": "application/json",
                                          "accept": "application/json, text/event-stream",
                                          "x-th-test": "1", "user-agent": "th-live-cases"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def call(name, args):
    return rpc("tools/call", {"name": name, "arguments": args})["result"]


txt = lambda r: r["content"][0]["text"]
sc = lambda r: r.get("structuredContent") or {}

CASES = [
    ("P1 what growth KO's price requires", "owner_earnings_valuation", {"ticker": "KO", "price": 70.0},
     lambda r: not r["isError"] and len(sc(r)["reverse_dcf"]) == 4 and sc(r)["inputs"]["long_bond"].get("date")),
    ("P2 compare three drinks makers", "compare_valuations",
     {"companies": [{"ticker": "KO", "price": 70.0}, {"ticker": "PEP", "price": 150.0},
                    {"ticker": "KDP", "price": 33.0}]},
     lambda r: not r["isError"] and len(sc(r)["ranked"]) == 3),
    ("P3 Corteva as filed, five years + TTM", "as_reported_financials", {"ticker": "CTVA", "years": 5, "ttm": True},
     lambda r: not r["isError"] and len(sc(r)["fiscal_years"]) == 5 and sc(r)["filings"]),
    ("P4 realized P&L, repo CSV", "realized_pnl", {"transactions_csv": EX["transactions"]},
     lambda r: not r["isError"] and sc(r)["summary"]["lots"] == 3 and sc(r)["summary"]["net"] == 1123.0
     and len(sc(r)["sells_without_basis"]) == 1),
    ("P4b realized P&L, E*TRADE download", "realized_pnl", {"transactions_csv": ETRADE},
     lambda r: not r["isError"] and sc(r)["read"]["format"] == "E*TRADE download"
     and abs(sc(r)["summary"]["net"] - 864.5) < 0.01),
    ("P5 stop coverage with journal", "stop_coverage_check",
     {"positions_csv": EX["portfolio"], "orders_csv": EX["orders"], "journal_text": JOURNAL},
     lambda r: not r["isError"] and sc(r)["counts"]["critical"] == 2),
    ("P6 rebalance order list", "rebalance_order_list",
     {"positions_csv": EX["portfolio"], "target_weights_pct": {"AAPL": 20, "MSFT": 20, "VTI": 30, "KHC": 0},
      "cash_on_hand": 10000, "cash_reserve": 5000, "prices": {"MSFT": 430.15}},
     lambda r: not r["isError"] and "Nothing was placed" in txt(r) and len(sc(r)["rows"]) == 4),
    ("N1 loss-making latest year: refused, no value", "owner_earnings_valuation",
     {"ticker": "KHC", "price": 25.0},
     lambda r: not r["isError"] and sc(r)["refused"] and "anchors" not in sc(r)),
    ("N3 weights over 100%: refused with the reason", "rebalance_order_list",
     {"positions_csv": EX["portfolio"], "target_weights_pct": {"AAPL": 60, "VTI": 60}},
     lambda r: r["isError"] and "over 100%" in txt(r)),
    ("N4 not an SEC filer", "as_reported_financials", {"ticker": "ZZZZQ"},
     lambda r: r["isError"] and "not found" in txt(r)),
]


def main():
    bad = blocked = 0
    for label, tool, args, ok in CASES:
        t0 = time.time()
        r = call(tool, args)
        if r["isError"] and "SEC_USER_AGENT" in txt(r):
            state = "BLOCKED"
            blocked += 1
        else:
            try:
                state = "PASS" if ok(r) else "FAIL"
            except Exception as e:
                state = f"FAIL ({type(e).__name__})"
            bad += state != "PASS"
        first = " | ".join(l for l in txt(r).split("\n") if l.strip())[:150]
        print(f"{state:7} {label} · {tool} · {int(1000 * (time.time() - t0))} ms · {first}")
    names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
    trade = [n for n in names if any(w in n for w in ("place", "submit", "preview", "execute", "trade"))]
    print(f"{'PASS' if not trade else 'FAIL':7} N2 no tool can place or preview an order · tools/list: {', '.join(names)}")
    bad += bool(trade)
    print(f"\n{len(CASES) + 1 - bad - blocked} pass, {bad} fail, {blocked} blocked on the SEC contact")
    sys.exit(1 if bad else (2 if blocked else 0))


if __name__ == "__main__":
    main()
