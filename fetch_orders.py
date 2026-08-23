"""Pull every order E*TRADE has on record into data/orders.csv.

This is the endpoint nothing else in the repo touched, and its absence is why
two execution gaps in one month had to be found by hand:

  * 2026-08-10 — a GOOD_FOR_DAY limit at $216 expired overnight; the stock
    traded to $215.71 the next session with no order in the market.
  * 2026-08-20 — after a deliberate concentration into one name, the orders
    endpoint returned zero open orders: the book's only remaining risk control
    existed solely on paper for a window after entry.

Both are the same question — *is the gate I believe is live actually resting at
the broker?* — and until now no script could ask it. `gate_check.py` answers it
off this CSV.

Traps this encodes:

  * **A 204 No Content is the normal answer when nothing matches.** The orders
    endpoint returns an empty body rather than an empty list, so a naive
    `.json()` raises on a perfectly good response. Handled in `etrade.get()`.
  * **No date range returns a short window, silently** — same shape as the
    transactions endpoint. We always send an explicit one.
  * **`status` filters server-side and the default is not OPEN.** We pull
    everything in the window on purpose: an EXPIRED row is the diagnostic for
    the 8/10 failure, and filtering to OPEN would have hidden it.
  * **One order carries a list of OrderDetail, each carrying a list of
    Instrument.** A multi-leg order is several rows here, one per instrument,
    sharing an orderId.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import etrade


def _mmddyyyy(s: str) -> str:
    return datetime.strptime(s, "%Y-%m-%d").strftime("%m%d%Y")


def _ts(v):
    """Epoch ms -> datetime, tolerating absent/zero."""
    if v in (None, "", 0, "0"):
        return None
    return pd.to_datetime(int(v), unit="ms")


def fetch_account(account_id_key: str, start: str, end: str) -> list[dict]:
    """Every order in the window, paginated. Returns raw E*TRADE Order dicts."""
    params = {"count": 100, "fromDate": _mmddyyyy(start), "toDate": _mmddyyyy(end)}
    orders, marker = [], None
    while True:
        if marker:
            params["marker"] = marker
        data = etrade.get(f"/v1/accounts/{account_id_key}/orders", params)
        resp = data.get("OrdersResponse", {}) or {}
        orders.extend(resp.get("Order", []) or [])
        marker = resp.get("marker")
        if not marker:
            break
    return orders


def flatten(orders: list[dict], account_id: str | None = None) -> list[dict]:
    """One row per (order, detail, instrument). Keeps the fields a gate needs."""
    rows = []
    for o in orders:
        for d in o.get("OrderDetail", []) or []:
            for inst in d.get("Instrument", []) or []:
                prod = inst.get("Product", {}) or {}
                rows.append({
                    "_accountId": account_id,
                    "orderId": o.get("orderId"),
                    "orderType": o.get("orderType"),
                    "status": d.get("status"),
                    "orderTerm": d.get("orderTerm"),
                    "priceType": d.get("priceType"),
                    "symbol": prod.get("symbol") or inst.get("symbolDescription"),
                    "securityType": prod.get("securityType"),
                    "orderAction": inst.get("orderAction"),
                    "orderedQuantity": inst.get("orderedQuantity"),
                    "filledQuantity": inst.get("filledQuantity"),
                    "limitPrice": d.get("limitPrice"),
                    "stopPrice": d.get("stopPrice"),
                    "stopLimitPrice": d.get("stopLimitPrice"),
                    "averageExecutionPrice": inst.get("averageExecutionPrice"),
                    "placedTime": _ts(d.get("placedTime")),
                    "executedTime": _ts(d.get("executedTime")),
                    "orderValue": d.get("orderValue"),
                    "allOrNone": d.get("allOrNone"),
                    "marketSession": d.get("marketSession"),
                    # An OCO/bracket leg points at its sibling; without these a
                    # bracket reads as two independent, over-committed orders.
                    "replacesOrderId": d.get("replacesOrderId"),
                    "replacedByOrderId": d.get("replacedByOrderId"),
                })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="YYYY-MM-DD (default: 90 days ago)")
    ap.add_argument("--end", help="YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    start = args.start or (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    end = args.end or datetime.now().strftime("%Y-%m-%d")
    print(f"Window: {start} -> {end}\n")

    raw, rows = [], []
    for acct in etrade.list_accounts():
        key = acct["accountIdKey"]
        print(f"Account {acct.get('accountId')} ({acct.get('accountDesc')})…")
        got = fetch_account(key, start, end)
        raw.extend(got)
        rows.extend(flatten(got, acct.get("accountId")))
        print(f"  {len(got)} orders -> {len(rows)} legs so far")

    Path("data").mkdir(exist_ok=True)
    # The raw payload is kept because the field mapping above is coded against
    # E*TRADE's documented shape and the sandbox has zero orders to validate it
    # (see SCHEMA.md) — so the first live run is the first real test of it.
    Path("data/orders_raw.json").write_text(json.dumps(raw, indent=2))
    df = pd.DataFrame(rows)
    df.to_csv("data/orders.csv", index=False)
    print(f"\nWrote {len(df)} order legs -> data/orders.csv"
          f"  (raw payload -> data/orders_raw.json)")

    if df.empty:
        print("\nNo orders in the window. That is a legitimate answer — and it is "
              "also exactly what 2026-08-20 looked like. Run gate_check.py.")
        return

    print("\n=== by status ===")
    print(df["status"].value_counts().to_string())
    resting = df[df["status"].isin(["OPEN", "PARTIAL", "CANCEL_REQUESTED"])]
    print(f"\n=== {len(resting)} resting leg(s) right now ===")
    if len(resting):
        cols = ["orderId", "status", "orderTerm", "priceType", "symbol",
                "orderAction", "orderedQuantity", "limitPrice", "stopPrice"]
        print(resting[cols].to_string(index=False))


if __name__ == "__main__":
    main()
