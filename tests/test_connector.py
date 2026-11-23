"""The connector (api/mcp.py + connector/). Offline: SEC and Treasury are stubbed,
and the conftest tripwire still stops any E*TRADE session.

What these pin: the connector computes the same numbers as the CLI scripts it
wraps, a refusal replaces a value, pasted exports in three shapes are read the
same way, the listing rules (titles, read-only annotations, descriptions that
describe), and that no response can carry a NaN.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

import load  # noqa: E402
import mcp  # noqa: E402
import owner_earnings as oe  # noqa: E402
import realized_pnl as rp  # noqa: E402
import rebalance as rb  # noqa: E402
import sec_financials as sf  # noqa: E402
from connector import tools, treasury  # noqa: E402

DATA = ROOT / "data"


# ---------------------------------------------------------------- SEC stub

def _flow(vals, tag_years):
    return {"units": {"USD": [
        {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "form": "10-K", "fy": y + 1,
         "fp": "FY", "accn": f"0000000000-{y + 1 - 2000:02d}-000001", "filed": f"{y + 1}-02-15"}
        for y, v in zip(tag_years, vals)]}}


def _stock(vals, tag_years):
    return {"units": {"USD": [
        {"end": f"{y}-12-31", "val": v, "form": "10-K", "fy": y + 1, "fp": "FY",
         "accn": f"0000000000-{y + 1 - 2000:02d}-000001", "filed": f"{y + 1}-02-15"}
        for y, v in zip(tag_years, vals)]}}


YEARS = list(range(2019, 2026))
M = 1e6


def _company(ni):
    """Seven years: revenue +5%/yr, D&A 30, capex 35, CFO = NI + 30."""
    rev = [1000 * M * 1.05 ** i for i in range(7)]
    return {"facts": {
        "us-gaap": {
            "Revenues": _flow(rev, YEARS),
            "GrossProfit": _flow([r * 0.4 for r in rev], YEARS),
            "NetIncomeLoss": _flow(ni, YEARS),
            "NetCashProvidedByUsedInOperatingActivities": _flow([n + 30 * M for n in ni], YEARS),
            "PaymentsToAcquirePropertyPlantAndEquipment": _flow([35 * M] * 7, YEARS),
            "DepreciationDepletionAndAmortization": _flow([30 * M] * 7, YEARS),
            "Depreciation": _flow([25 * M] * 7, YEARS),
            "AmortizationOfIntangibleAssets": _flow([5 * M] * 7, YEARS),
            "StockholdersEquity": _stock([500 * M] * 7, YEARS),
            "Goodwill": _stock([50 * M] * 7, YEARS),
            "IntangibleAssetsNetExcludingGoodwill": _stock([10 * M] * 7, YEARS),
            "CashAndCashEquivalentsAtCarryingValue": _stock([50 * M] * 7, YEARS),
            "LongTermDebtNoncurrent": _stock([100 * M] * 7, YEARS),
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2026-01-31", "val": 100 * M, "filed": "2026-02-15"}]}}},
    }}


HEALTHY = _company([100 * M * 1.06 ** i for i in range(7)])
LOSS = _company([100 * M] * 6 + [-200 * M])
TICKERS = {"0": {"cik_str": 1, "ticker": "HLTH", "title": "Healthy Co"},
           "1": {"cik_str": 2, "ticker": "LOSS", "title": "Loss Co"},
           "2": {"cik_str": 3, "ticker": "BRK-B", "title": "Class B Co"}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    pages = {sf.TICKERS: TICKERS, sf.FACTS.format(cik=1): HEALTHY,
             sf.FACTS.format(cik=2): LOSS, sf.FACTS.format(cik=3): HEALTHY}

    def fake_get(url):
        if url not in pages:
            raise AssertionError(f"unexpected SEC url {url}")
        return pages[url]

    def no_net(*a, **k):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(sf, "get", fake_get)
    monkeypatch.setattr(sf, "_fetch", no_net)
    monkeypatch.setattr(treasury, "_year", no_net)
    treasury._memo.clear()
    monkeypatch.setenv("TH_NO_ANALYTICS", "1")
    yield


def rpc(method, params=None):
    out = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    json.dumps(mcp.clean(out), allow_nan=False)   # every response must be valid JSON
    return out


def call(name, **args):
    return rpc("tools/call", {"name": name, "arguments": args})["result"]


def text(r):
    return r["content"][0]["text"]


# ---------------------------------------------------------------- listing rules

DOOR_NAMES = ["is_this_stock_undervalued", "growth_priced_into_stock", "which_stock_is_cheapest",
              "free_cash_flow_history", "dividends_and_buybacks",
              "short_vs_long_term_capital_gains", "wash_sale_check",
              "tax_loss_harvest_candidates", "trading_win_rate", "positions_without_stop_loss"]


def test_six_engines_plus_theme_doors_each_titled_read_only_and_annotated():
    t = rpc("tools/list")["result"]["tools"]
    assert [x["name"] for x in t] == ["as_reported_financials", "owner_earnings_valuation",
                                      "compare_valuations", "realized_pnl",
                                      "stop_coverage_check", "rebalance_order_list"] + DOOR_NAMES
    for x in t:
        a = x["annotations"]
        assert x["title"] and a["title"] == x["title"]
        assert a["readOnlyHint"] is True and a["destructiveHint"] is False
        assert isinstance(a["openWorldHint"], bool)


def test_descriptions_describe_and_never_instruct_or_advise():
    for x in rpc("tools/list")["result"]["tools"]:
        assert not __import__("re").search(
            r"\b(always|never|must|do not|don't|show|tell the user|you should|recommend|"
            r"should buy|should sell)\b", x["description"], __import__("re").I), x["name"]


def test_initialize_names_the_listing_and_says_it_cannot_trade():
    r = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "1"}})["result"]
    assert r["serverInfo"]["title"] == "Trading Harness"
    assert r["protocolVersion"] == "2025-06-18"
    ins = r["instructions"]
    assert "cannot place, preview or stage an order" in ins and "not investment advice" in ins


def test_a_notification_gets_no_body_and_an_unknown_method_an_error():
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert rpc("nope")["error"]["code"] == -32601


def test_nothing_in_the_server_path_imports_the_broker_client():
    """No E*TRADE anywhere in the request path: the connector package never
    imports etrade, place_order or the fetchers."""
    src = "".join(p.read_text() for p in (ROOT / "connector").glob("*.py")) + \
        (ROOT / "api" / "mcp.py").read_text()
    for mod in ("etrade", "place_order", "fetch_orders", "fetch_portfolio",
                "fetch_transactions", "auth_cli"):
        assert f"import {mod}" not in src and f"from {mod}" not in src


# ---------------------------------------------------------------- valuation

def test_valuation_matches_the_cli_function_it_wraps():
    r = call("owner_earnings_valuation", ticker="HLTH", price=20, long_bond_pct=5)
    v = oe.valuation("HLTH", 20, 5)
    s = r["structuredContent"]
    assert not r["isError"] and s["refused"] is False
    assert s["midpoint_per_share"] == round(v["midpoint_per_share"], 2)
    assert [a["per_share"] for a in s["anchors"]] == [round(a["per_share"], 2) for a in v["anchors"]]


def test_reverse_dcf_recovers_the_growth_a_price_was_built_from():
    v = oe.valuation("HLTH")
    price = oe.dcf(v["norm_oe"], 0.04, 0.10, 0.025) / (100 * M)
    r = call("owner_earnings_valuation", ticker="HLTH", price=price, long_bond_pct=5)
    at10 = next(x for x in r["structuredContent"]["reverse_dcf"] if x["required_return_pct"] == 10)
    assert at10["implied_owner_earnings_growth"] == pytest.approx(0.04, abs=1e-3)


def test_a_loss_in_the_latest_year_is_refused_and_no_value_is_returned():
    r = call("owner_earnings_valuation", ticker="LOSS", price=20, long_bond_pct=5)
    s = r["structuredContent"]
    assert not r["isError"] and s["refused"] is True
    assert "LOSS-MAKING" in s["refusal_reason"]
    assert "anchors" not in s and "reverse_dcf" not in s
    assert "Refused" in text(r)


def test_no_price_returns_the_history_and_says_why_the_market_part_is_missing():
    r = call("owner_earnings_valuation", ticker="HLTH")
    assert "No price given" in text(r) and "anchors" not in r["structuredContent"]
    assert len(r["structuredContent"]["owner_earnings_by_year"]) == 7


def test_the_long_bond_defaults_to_treasurys_latest_30_year(monkeypatch):
    rows = [{"Date": "11/19/2026", "30 Yr": "5.40"}, {"Date": "11/18/2026", "30 Yr": "5.41"}]
    monkeypatch.setattr(treasury, "_year", lambda y: rows)
    r = call("owner_earnings_valuation", ticker="HLTH", price=20)
    lb = r["structuredContent"]["inputs"]["long_bond"]
    assert (lb["pct"], lb["date"]) == (5.40, "2026-11-19")
    assert "2026-11-19" in text(r)


def test_compare_refuses_the_loss_maker_instead_of_dropping_it():
    r = call("compare_valuations", companies=[{"ticker": "HLTH", "price": 20},
                                              {"ticker": "LOSS", "price": 20},
                                              {"ticker": "NOPE", "price": 5}], long_bond_pct=5)
    s = r["structuredContent"]
    by = {x["ticker"]: x for x in s["ranked"]}
    assert by["LOSS"]["refused"] and by["LOSS"]["gap"] is None
    assert by["HLTH"]["gap"] is not None
    assert s["excluded"][0]["ticker"] == "NOPE"


def test_a_share_class_written_with_a_dot_resolves():
    r = call("as_reported_financials", ticker="brk.b", years=3)
    assert not r["isError"] and r["structuredContent"]["fiscal_years"] == [2023, 2024, 2025]


def test_an_unknown_ticker_is_an_answer_not_a_crash():
    r = call("as_reported_financials", ticker="ZZZZ")
    assert r["isError"] and "not found" in text(r)


def test_missing_sec_contact_says_so(monkeypatch):
    def unset(url):
        raise sf.MissingContact(sf.NO_CONTACT)
    monkeypatch.setattr(sf, "get", unset)
    r = call("as_reported_financials", ticker="HLTH")
    assert r["isError"] and "SEC_USER_AGENT" in text(r)


# ---------------------------------------------------------------- your own data

def test_realized_pnl_on_the_repo_csv_matches_the_cli():
    r = call("realized_pnl", transactions_csv=(DATA / "example_transactions.csv").read_text())
    c, o = rp.realized(load.load(DATA / "example_transactions.csv"))
    st = rp.summary(c, o)
    s = r["structuredContent"]["summary"]
    assert (s["lots"], s["net"], s["excluded_orphan_sells"]) == (st["lots"], round(st["net"], 2),
                                                                 st["excluded_orphan_sells"])


ETRADE = """For Account:,#####1234

Download Date:,11/20/2026

TransactionDate,TransactionType,SecurityType,Symbol,Quantity,Amount,Price,Commission,Description
11/14/25,Sold,EQ,KO,-100,6984.50,69.85,0.50,COCA COLA CO
10/02/25,Dividend,EQ,KO,0,51.00,0,0,COCA COLA CO
03/03/25,Sold,EQ,AAPL,-5,1190.00,238.00,0,APPLE INC
01/06/25,Bought,EQ,KO,100,-6120.00,61.20,0,COCA COLA CO
01/06/25,Bought,OPTN,AAPL Jan 16 '26 $250 Call,1,-420.00,4.20,0.65,CALL AAPL
"""


def test_etrade_download_preamble_fees_options_and_a_sell_without_basis():
    r = call("realized_pnl", transactions_csv=ETRADE)
    s = r["structuredContent"]
    assert s["read"]["format"] == "E*TRADE download"
    assert s["read"]["options_rows_set_aside"] == 1
    assert s["summary"]["lots"] == 1
    assert s["summary"]["net"] == pytest.approx((69.85 - 61.20) * 100 - 0.50)
    assert s["best_lots"][0]["hold_days"] == 312
    assert [o["symbol"] for o in s["sells_without_basis"]] == ["AAPL"]


def test_newest_first_generic_export_keeps_same_day_lots_in_time_order():
    csv = ("Date,Action,Symbol,Quantity,Price ($),Fees & Comm\n"
           "09/02/2025,Sell,KO,40,69.85,0\n"
           "01/06/2025,Buy,KO,100,61.20,1.00\n"
           "01/06/2025,Buy,KO,10,61.30,0\n")
    s = call("realized_pnl", transactions_csv=csv)["structuredContent"]
    # newest first, so the 10-share row is the older buy and closes first
    assert [(l["qty"], l["buy_price"]) for l in s["worst_lots"]] in (
        [(10, 61.3), (30, 61.2)], [(30, 61.2), (10, 61.3)])
    assert s["read"]["file_order"] == "newest first (reversed)"


def test_a_csv_with_no_trades_says_what_it_saw():
    r = call("realized_pnl", transactions_csv="date,action,symbol,quantity,price\n"
                                              "2025-01-02,Dividend,KO,0,0\n")
    assert r["isError"] and "Dividend" in text(r)


def test_stop_check_on_the_repo_examples_finds_what_the_cli_finds():
    import gate_check as gc
    import pandas as pd
    r = call("stop_coverage_check",
             positions_csv=(DATA / "example_portfolio.csv").read_text(),
             orders_csv=(DATA / "example_orders.csv").read_text())
    msgs = [f["message"] for f in r["structuredContent"]["findings"]]
    pf = pd.read_csv(DATA / "example_portfolio.csv")
    cli = [m for _, m in gc.tier1(pf, pd.read_csv(DATA / "example_orders.csv"), 28.0)]
    # same findings; percentages are recomputed from market value, so compare the kinds
    assert sum("NO resting stop" in m for m in msgs) == sum("NO resting stop" in m for m in cli) == 1
    assert sum("GOOD_FOR_DAY" in m for m in msgs) == sum("GOOD_FOR_DAY" in m for m in cli)
    assert sum("over-commit" in m for m in msgs) == sum("over-commit" in m for m in cli)


def test_none_means_zero_resting_orders_and_reads_as_a_warning():
    r = call("stop_coverage_check", positions_csv="symbol,quantity,last_price\nKO,200,70\nAAPL,100,230",
             orders_csv="none", cap_pct=50)
    s = r["structuredContent"]
    assert s["counts"]["critical"] == 1           # AAPL is 62% with no stop
    assert any("Zero resting orders" in f["message"] for f in s["findings"])


def test_generic_orders_with_a_day_stop_and_no_status_column():
    r = call("stop_coverage_check",
             positions_csv="Symbol,Qty,Last Price $\nKO,200,70.00",
             orders_csv="symbol,side,quantity,order type,price,duration\nKO,Sell,200,Stop,62,Day",
             cap_pct=25)
    s = r["structuredContent"]
    assert any("GOOD_FOR_DAY" in f["message"] for f in s["findings"])
    assert any("stop covers 200" in f["message"] for f in s["findings"])
    assert "status_assumed" in s["read"]["orders"]


def test_rebalance_matches_the_cli_plan():
    import json as _j
    cfg = _j.loads((ROOT / "targets.example.json").read_text())
    import pandas as pd
    cli, _ = rb.plan_frame(pd.read_csv(DATA / "example_portfolio.csv"), cfg)
    r = call("rebalance_order_list", positions_csv=(DATA / "example_portfolio.csv").read_text(),
             target_weights_pct={k: v * 100 for k, v in cfg["targets"].items()},
             cash_on_hand=cfg["cash_on_hand"], cash_reserve=cfg["cash_reserve"], prices=cfg["prices"])
    got = {x["symbol"]: x["change_usd"] for x in r["structuredContent"]["rows"]}
    assert got == {row["symbol"]: row["delta_$"] for _, row in cli.iterrows()}
    assert "Nothing was placed" in text(r)


def test_weights_over_100_percent_are_refused():
    r = call("rebalance_order_list", positions_csv="symbol,quantity,last_price\nKO,10,70",
             target_weights_pct={"KO": 60, "PEP": 50})
    assert r["isError"] and "over 100%" in text(r)


def test_bad_arguments_get_a_sentence_back():
    assert call("realized_pnl")["isError"]
    r = call("compare_valuations", companies=[{"ticker": "KO"}])
    assert r["isError"] and "2 to 8" in text(r)
    r = call("owner_earnings_valuation", ticker="KO", price="seventy")
    assert r["isError"] and "number" in text(r)


# ---------------------------------------------------------------- theme doors

def test_door_wraps_its_engine_with_the_same_numbers():
    a = call("is_this_stock_undervalued", ticker="HLTH", price=20, long_bond_pct=5)["structuredContent"]
    b = call("owner_earnings_valuation", ticker="HLTH", price=20, long_bond_pct=5)["structuredContent"]
    assert a["midpoint_per_share"] == b["midpoint_per_share"]
    g = call("growth_priced_into_stock", ticker="HLTH", price=20, long_bond_pct=5)
    assert text(g).startswith("Growth the price requires")


def test_free_cash_flow_is_operating_cash_flow_less_capex():
    r = call("free_cash_flow_history", ticker="HLTH")
    rows = r["structuredContent"]["rows"]
    assert rows and all(x["free_cash_flow"] == x["cfo"] - x["capex"] for x in rows if x["cfo"])


SPLIT = """date,action,symbol,quantity,price,fee
2024-01-02,Buy,AAA,10,100,0
2024-06-03,Sell,AAA,10,90,0
2024-06-20,Buy,AAA,10,92,0
2023-01-03,Buy,BBB,5,50,0
2024-03-01,Sell,BBB,5,70,0
"""


def test_short_and_long_term_split_by_the_one_year_line():
    s = call("short_vs_long_term_capital_gains", transactions_csv=SPLIT)["structuredContent"]
    y = next(r for r in s["by_year"] if r["tax_year"] == 2024)
    assert y["short"]["net"] == -100 and y["long"]["net"] == 100


def test_wash_sale_finds_the_buyback_inside_30_days():
    s = call("wash_sale_check", transactions_csv=SPLIT)["structuredContent"]
    assert [h["symbol"] for h in s["possible_wash_sales"]] == ["AAA"]


def test_harvest_lists_only_losers():
    pos = "symbol,quantity,marketValue,totalGain\nAAA,10,900,-100\nBBB,5,350,100\n"
    s = call("tax_loss_harvest_candidates", positions_csv=pos)["structuredContent"]
    assert [c["symbol"] for c in s["candidates"]] == ["AAA"] and s["total_unrealized_loss"] == -100


def test_door_schema_misses_get_a_sentence_and_a_loggable_detail():
    from connector import tools as T
    text_, _, info, err = T.call("wash_sale_check", {"csv": "x"})
    assert err and "transactions_csv" in text_ and info["err_detail"].startswith("wash_sale_check:")
