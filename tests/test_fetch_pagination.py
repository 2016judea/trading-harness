"""The two paginated pulls: an inclusive marker must not duplicate rows, a
zero-order pull must still write a CSV, and epoch times land on the ET clock."""
from __future__ import annotations

import pandas as pd

import etrade
import fetch_orders
import fetch_transactions


def _pager(key, pages):
    """Stub etrade.get: serve `pages` in order, keyed by the marker sent."""
    calls = []

    def get(path, params=None):
        calls.append(dict(params or {}))
        i = 0 if not (params or {}).get("marker") else int(params["marker"])
        return pages[i]

    return get, calls


def test_transactions_marker_is_inclusive_so_the_boundary_row_is_dropped(monkeypatch):
    boundary = {"transactionId": 50, "transactionType": "Bought"}
    pages = [
        {"TransactionListResponse": {"Transaction": [{"transactionId": i} for i in range(1, 50)] + [boundary],
                                     "marker": "1"}},
        {"TransactionListResponse": {"Transaction": [boundary] + [{"transactionId": i} for i in range(51, 60)]}},
    ]
    get, calls = _pager("Transaction", pages)
    monkeypatch.setattr(etrade, "get", get)
    rows = fetch_transactions.fetch_account("K", "2026-01-01", "2026-06-01")
    ids = [r["transactionId"] for r in rows]
    assert ids == list(range(1, 60))
    assert len(calls) == 2 and calls[1]["marker"] == "1"
    assert calls[0]["startDate"] == "01012026" and calls[0]["endDate"] == "06012026"


def test_orders_marker_is_inclusive_too(monkeypatch):
    boundary = {"orderId": 100, "OrderDetail": []}
    pages = [
        {"OrdersResponse": {"Order": [{"orderId": 99, "OrderDetail": []}, boundary], "marker": "1"}},
        {"OrdersResponse": {"Order": [boundary, {"orderId": 101, "OrderDetail": []}]}},
    ]
    get, _ = _pager("Order", pages)
    monkeypatch.setattr(etrade, "get", get)
    assert [o["orderId"] for o in fetch_orders.fetch_account("K", "2026-01-01", "2026-06-01")] == [99, 100, 101]


def test_zero_orders_still_writes_a_header_that_gate_check_can_read(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(etrade, "list_accounts", lambda: [{"accountIdKey": "K", "accountId": "1"}])
    monkeypatch.setattr(fetch_orders, "fetch_account", lambda *a, **k: [])
    monkeypatch.setattr("sys.argv", ["fetch_orders.py"])
    fetch_orders.main()
    out = tmp_path / "data" / "orders.csv"
    assert out.read_text().strip() != ""               # was a bare "\n"
    df = pd.read_csv(out)                              # used to raise EmptyDataError
    assert list(df.columns) == fetch_orders.COLUMNS and df.empty


def test_placed_time_is_on_the_exchange_clock():
    # 2026-09-01 16:23:53 UTC is a 12:23 ET fill.
    t = fetch_orders._ts(1788279833061)
    assert t.strftime("%Y-%m-%d %H:%M") == "2026-09-01 12:23"
    assert fetch_orders._ts(0) is None and fetch_orders._ts(None) is None


def test_flatten_one_row_per_instrument():
    o = {"orderId": 7, "orderType": "EQ", "OrderDetail": [{
        "status": "OPEN", "orderTerm": "GOOD_UNTIL_CANCEL", "priceType": "STOP",
        "stopPrice": 75.0, "placedTime": 1788279833061,
        "Instrument": [{"Product": {"symbol": "ACME", "securityType": "EQ"},
                        "orderAction": "SELL", "orderedQuantity": 1000, "filledQuantity": 0.0}]}]}
    rows = fetch_orders.flatten([o], "1")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ACME" and rows[0]["stopPrice"] == 75.0
    assert set(rows[0]) == set(fetch_orders.COLUMNS)
