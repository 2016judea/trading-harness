"""place_order.py, driven entirely against stubs. Nothing here previews or
places an order; `conftest` trips any attempt to open a session."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import etrade
import place_order as po

ACCT = {"accountIdKey": "K", "accountId": "1"}


def _order(oid, status, ptype, action="SELL", qty=1000, filled=0, stop=None, limit=None, term="GOOD_UNTIL_CANCEL"):
    return {"orderId": oid, "OrderDetail": [{
        "status": status, "priceType": ptype, "orderTerm": term,
        "stopPrice": stop, "limitPrice": limit,
        "Instrument": [{"Product": {"symbol": "ACME"}, "orderAction": action,
                        "orderedQuantity": qty, "filledQuantity": filled}]}]}


def test_resting_sends_a_date_window_and_follows_the_marker(monkeypatch):
    calls = []
    pages = {None: {"OrdersResponse": {"Order": [_order(1, "OPEN", "STOP", stop=75.0)], "marker": "m2"}},
             "m2": {"OrdersResponse": {"Order": [_order(1, "OPEN", "STOP", stop=75.0),   # inclusive repeat
                                                 _order(2, "EXECUTED", "MARKET"),
                                                 _order(3, "PARTIAL", "LIMIT", limit=88.0, filled=500)]}}}

    def get(path, params=None):
        calls.append(dict(params))
        return pages[params.get("marker")]

    monkeypatch.setattr(etrade, "get", get)
    rest = po.resting(ACCT)
    assert [r["orderId"] for r in rest] == [1, 3]            # executed dropped, no duplicate
    assert rest[1]["qty"] == 500                              # ordered minus filled
    assert len(calls) == 2 and calls[1]["marker"] == "m2"
    for c in calls:
        assert len(c["fromDate"]) == 8 and len(c["toDate"]) == 8, c   # MMDDYYYY, always sent


def test_r4_sees_a_resting_stop_the_old_narrow_window_would_have_hidden():
    held = {"ACME": {"qty": 1000, "last": 83.0}}
    rest = [{"symbol": "ACME", "action": "SELL", "qty": 1000, "priceType": "STOP"}]
    leg = {"symbol": "ACME", "action": "SELL", "qty": 1000, "priceType": "STOP",
           "stopPrice": 75.0, "limitPrice": None, "orderTerm": "GOOD_UNTIL_CANCEL"}
    with pytest.raises(po.Refused, match="R4"):
        po.check_rails(leg, held, rest, last=83.0, allow_buy=False, force_day=False)


@pytest.mark.parametrize("ptype,px,last,rule", [
    ("STOP", 85.0, 83.0, "R5"),      # stop above last -> instant market sell
    ("LIMIT", 80.0, 83.0, "R5"),     # limit below last -> instant fill
    ("STOP", 7.5, 83.0, "R6"),       # decimal slip
    ("MARKET", None, 83.0, "R10"),
])
def test_rails_refuse_the_expensive_typos(ptype, px, last, rule):
    held = {"ACME": {"qty": 1000, "last": last}}
    leg = {"symbol": "ACME", "action": "SELL", "qty": 1000, "priceType": ptype,
           "stopPrice": px if ptype == "STOP" else None,
           "limitPrice": px if ptype == "LIMIT" else None, "orderTerm": "GOOD_UNTIL_CANCEL"}
    with pytest.raises(po.Refused, match=rule):
        po.check_rails(leg, held, [], last, allow_buy=False, force_day=False)


def test_buy_is_refused_without_the_flag_and_day_stop_without_force():
    held = {"ACME": {"qty": 1000, "last": 83.0}}
    buy = {"symbol": "ACME", "action": "BUY", "qty": 1, "priceType": "LIMIT",
           "limitPrice": 80.0, "stopPrice": None, "orderTerm": "GOOD_UNTIL_CANCEL"}
    with pytest.raises(po.Refused, match="R2"):
        po.check_rails(buy, held, [], 83.0, allow_buy=False, force_day=False)
    day_stop = {"symbol": "ACME", "action": "SELL", "qty": 1000, "priceType": "STOP",
                "stopPrice": 75.0, "limitPrice": None, "orderTerm": "GOOD_FOR_DAY"}
    with pytest.raises(po.Refused, match="R7"):
        po.check_rails(day_stop, held, [], 83.0, allow_buy=False, force_day=False)


def _wire(monkeypatch, held, rest, last, posted):
    monkeypatch.setattr(po, "positions", lambda acct: held)
    monkeypatch.setattr(po, "resting", lambda acct: list(rest))
    monkeypatch.setattr(po, "last_price", lambda sym: last)

    def post(path, payload):
        posted.append(path)
        if path.endswith("/preview"):
            return {"PreviewOrderResponse": {"PreviewIds": [{"previewId": 1}], "Messages": {}}}
        return {"PlaceOrderResponse": {"OrderIds": [{"orderId": 999}]}}

    monkeypatch.setattr(etrade, "post", post)


def test_dry_run_previews_and_never_places(monkeypatch, tmp_path):
    posted = []
    _wire(monkeypatch, {"ACME": {"qty": 1000, "last": 83.0}}, [], 83.0, posted)
    monkeypatch.setattr(po, "LOG", tmp_path / "placed.log")
    legs = po.journal_legs(Path(__file__).parents[1] / "JOURNAL.template.md")
    for l in legs:
        l["symbol"] = "ACME"; l["qty"] = min(l["qty"], 1000)
    args = SimpleNamespace(place=False, i_am_buying=False, force_day=False)
    assert po._run(ACCT, legs, args) == 0
    assert posted and all(p.endswith("/orders/preview") for p in posted)
    assert not (tmp_path / "placed.log").exists()


def test_a_refused_stop_halts_that_symbols_take_profits_when_placing(monkeypatch, tmp_path, capsys):
    # Stop at $85 with last $83 is refused by R5. The two limits are valid on
    # their own -- and placing them would leave take-profits resting on a
    # position with no floor, the half the stop-first ordering exists to avoid.
    posted = []
    _wire(monkeypatch, {"ACME": {"qty": 1000, "last": 83.0}}, [], 83.0, posted)
    monkeypatch.setattr(po, "LOG", tmp_path / "placed.log")
    legs = [
        {"symbol": "ACME", "action": "SELL", "qty": 1000, "priceType": "STOP", "stopPrice": 85.0,
         "limitPrice": None, "orderTerm": "GOOD_UNTIL_CANCEL", "label": "stop"},
        {"symbol": "ACME", "action": "SELL", "qty": 500, "priceType": "LIMIT", "stopPrice": None,
         "limitPrice": 88.0, "orderTerm": "GOOD_UNTIL_CANCEL", "label": "tp1"},
        {"symbol": "ACME", "action": "SELL", "qty": 500, "priceType": "LIMIT", "stopPrice": None,
         "limitPrice": 90.5, "orderTerm": "GOOD_UNTIL_CANCEL", "label": "tp2"},
    ]
    args = SimpleNamespace(place=True, i_am_buying=False, force_day=False)
    assert po._run(ACCT, legs, args) == 1
    assert posted == []                                   # nothing previewed or placed
    out = capsys.readouterr().out
    assert out.count("NOT PLACED") == 2 and "R5" in out
    # In a dry run the limits still get their rail read-out, so the plan is reviewable.
    posted.clear()
    args = SimpleNamespace(place=False, i_am_buying=False, force_day=False)
    po._run(ACCT, legs, args)
    assert len(posted) == 2 and all(p.endswith("/preview") for p in posted)


def test_journal_legs_parse_the_template_stop_first():
    legs = po.journal_legs(Path(__file__).parents[1] / "JOURNAL.template.md")
    assert [l["priceType"] for l in legs] == ["STOP", "LIMIT", "LIMIT"]
    assert legs[0] == {"symbol": "TICKER", "action": "SELL", "qty": 1000, "priceType": "STOP",
                       "stopPrice": 75.0, "limitPrice": None, "orderTerm": "GOOD_UNTIL_CANCEL",
                       "label": "TICKER STOP — STOP $75.00"}


def test_an_exit_plan_with_no_rows_is_an_error_not_nothing_to_do(tmp_path):
    j = tmp_path / "J.md"
    j.write_text("## ⚑ EXIT PLAN — ACME\n\nsell it at some point\n")
    with pytest.raises(po.Refused, match="no readable rows"):
        po.journal_legs(j)


def test_body_formats_prices_and_quantity_as_strings():
    leg = {"symbol": "ACME", "action": "SELL", "qty": 1000, "priceType": "STOP",
           "stopPrice": 75.0, "limitPrice": None, "orderTerm": "GOOD_UNTIL_CANCEL"}
    body = po._body(leg, "ord1")
    o = body["Order"][0]
    assert o["stopPrice"] == "75.00" and "limitPrice" not in o
    assert o["Instrument"][0]["quantity"] == "1000"
    assert len(po._client_id()) <= 20 and po._client_id().isalnum()
