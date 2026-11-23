"""The deployed connector, called over HTTP. Opt-in, because it needs the network:

    TH_LIVE=1 python -m pytest tests/test_live_endpoint.py

tools/list, then each tool once with the repo's example data. The valuation tools
skip (not fail) while the server's SEC contact is unset: that is a deploy setting,
and scripts/set_sec_contact.sh is the fix.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("TH_LIVE") != "1", reason="set TH_LIVE=1 to hit the live endpoint")

URL = os.environ.get("TH_URL", "https://trading-harness-mcp.vercel.app/mcp")
ROOT = Path(__file__).resolve().parents[1]
EX = {n: (ROOT / "data" / f"example_{n}.csv").read_text() for n in ("transactions", "portfolio", "orders")}


def rpc(method, params=None):
    req = urllib.request.Request(URL, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                       "params": params or {}}).encode(),
                                 headers={"content-type": "application/json", "x-th-test": "1",
                                          "accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def call(name, **args):
    r = rpc("tools/call", {"name": name, "arguments": args})["result"]
    if r["isError"] and "SEC_USER_AGENT" in r["content"][0]["text"]:
        pytest.skip("live server has no SEC contact yet (scripts/set_sec_contact.sh)")
    return r


def test_tools_list_is_the_sixteen_read_only_tools():
    tools = rpc("tools/list")["result"]["tools"]
    assert len(tools) == 16
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in tools)


def test_realized_pnl():
    r = call("realized_pnl", transactions_csv=EX["transactions"])
    assert not r["isError"] and r["structuredContent"]["summary"]["net"] == 1123.0


def test_stop_coverage_check():
    r = call("stop_coverage_check", positions_csv=EX["portfolio"], orders_csv=EX["orders"])
    assert not r["isError"] and r["structuredContent"]["counts"]["critical"] == 2


def test_rebalance_order_list():
    r = call("rebalance_order_list", positions_csv=EX["portfolio"],
             target_weights_pct={"AAPL": 20, "MSFT": 20, "VTI": 30, "KHC": 0},
             cash_on_hand=10000, cash_reserve=5000, prices={"MSFT": 430.15})
    assert not r["isError"] and len(r["structuredContent"]["rows"]) == 4


def test_as_reported_financials():
    r = call("as_reported_financials", ticker="KO", years=3)
    assert not r["isError"] and len(r["structuredContent"]["fiscal_years"]) == 3


def test_owner_earnings_valuation():
    r = call("owner_earnings_valuation", ticker="KO", price=70.0)
    assert not r["isError"] and len(r["structuredContent"]["reverse_dcf"]) == 4


def test_compare_valuations():
    r = call("compare_valuations", companies=[{"ticker": "KO", "price": 70.0}, {"ticker": "PEP", "price": 150.0}])
    assert not r["isError"] and len(r["structuredContent"]["ranked"]) == 2
