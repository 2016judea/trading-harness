"""Pull full transaction history for every account into data/transactions.csv.

The Transactions API paginates (default 50). We page with the `marker` field
until E*TRADE stops returning one. Optional date filtering via --start/--end
(YYYY-MM-DD); note the API only retains a limited lookback window, so for a
complete multi-year archive supplement this with statement CSVs from the web UI.
"""
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import etrade


def _mmddyyyy(s: str) -> str:
    return datetime.strptime(s, "%Y-%m-%d").strftime("%m%d%Y")


def fetch_account(account_id_key: str, start=None, end=None) -> list[dict]:
    params = {"count": 50}
    if start:
        params["startDate"] = _mmddyyyy(start)
    if end:
        params["endDate"] = _mmddyyyy(end)

    rows, marker = [], None
    while True:
        if marker:
            params["marker"] = marker
        data = etrade.get(f"/v1/accounts/{account_id_key}/transactions", params)
        resp = data.get("TransactionListResponse", {})
        rows.extend(resp.get("Transaction", []))
        marker = resp.get("marker")
        if not marker:
            break
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD")
    args = ap.parse_args()

    # Without an explicit startDate the API returns only the last ~month, and it
    # rejects (error 2003) any date older than 3 years. So default to the full
    # available window: ~3 years ago -> today. Older history needs statement CSVs.
    start = args.start or (datetime.now() - timedelta(days=3 * 365 - 2)).strftime("%Y-%m-%d")
    end = args.end or datetime.now().strftime("%Y-%m-%d")
    print(f"Window: {start} -> {end} (API retains ~3 years)\n")

    all_rows = []
    for acct in etrade.list_accounts():
        key = acct["accountIdKey"]
        print(f"Account {acct.get('accountId')} ({acct.get('accountDesc')})…")
        rows = fetch_account(key, start, end)
        for r in rows:
            r["_accountId"] = acct.get("accountId")
        print(f"  {len(rows)} transactions")
        all_rows.extend(rows)

    Path("data").mkdir(exist_ok=True)
    df = pd.json_normalize(all_rows)
    out = Path("data") / "transactions.csv"
    df.to_csv(out, index=False)
    print(f"\nWrote {len(df)} rows -> {out}")


if __name__ == "__main__":
    main()
