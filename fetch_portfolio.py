"""Pull current positions (cost basis, market value, unrealized gain) to CSV."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import etrade

_FIELDS = [
    "symbolDescription", "quantity", "pricePaid", "costPershare", "totalCost",
    "marketValue", "totalGain", "totalGainPct", "daysGainPct", "pctOfPortfolio",
    "dateAcquired",
]


def fetch() -> pd.DataFrame:
    rows = []
    for acct in etrade.list_accounts():
        key = acct["accountIdKey"]
        data = etrade.get(f"/v1/accounts/{key}/portfolio", {"count": 250, "view": "COMPLETE"})
        for ap in data.get("PortfolioResponse", {}).get("AccountPortfolio", []):
            for p in ap.get("Position", []):
                comp = p.get("Complete", {})
                rows.append({
                    "_accountId": acct.get("accountId"),
                    "symbol": p.get("symbolDescription"),
                    "securityType": p.get("Product", {}).get("securityType"),
                    "quantity": p.get("quantity"),
                    "costPerShare": p.get("costPerShare"),
                    "totalCost": p.get("totalCost"),
                    "lastPrice": comp.get("lastTrade"),
                    "marketValue": p.get("marketValue"),
                    "totalGain": p.get("totalGain"),
                    "totalGainPct": p.get("totalGainPct"),
                    "pctOfPortfolio": p.get("pctOfPortfolio"),
                    "perform12Month": comp.get("perform12Month"),
                    "week52Low": comp.get("week52Low"),
                    "week52High": comp.get("week52High"),
                    "beta": comp.get("beta"),
                    "dateAcquired": pd.to_datetime(p.get("dateAcquired"), unit="ms"),
                })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = fetch().sort_values("marketValue", ascending=False)
    Path("data").mkdir(exist_ok=True)
    df.to_csv("data/portfolio.csv", index=False)
    print(f"Wrote {len(df)} positions -> data/portfolio.csv")
    print(f"Total market value: ${df['marketValue'].sum():,.0f}  "
          f"unrealized: ${df['totalGain'].sum():,.0f}")
