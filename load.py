"""Load data/transactions.csv into a tidy, analysis-ready DataFrame.

Normalizes E*TRADE's raw shape: epoch dates -> datetimes, and splits
securities trades (have a symbol/quantity) from cash movements (transfers,
fees, POS, bill pay). Import and call load() from a notebook or script.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_DATE_COLS = ["transactionDate", "postDate", "brokerage.settlementDate"]


def load(path: str | Path = "data/transactions.csv") -> pd.DataFrame:
    df = pd.read_csv(path)

    # E*TRADE returns epoch timestamps. 13-digit values are ms, 10-digit are s;
    # detect per-column from the median magnitude so either works on real data.
    for col in _DATE_COLS:
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce")
            unit = "ms" if s.dropna().median() > 1e11 else "s"
            df[col] = pd.to_datetime(s, unit=unit)

    df = df.sort_values("transactionDate").reset_index(drop=True)

    # Classify by transactionType into coarse buckets for analysis. A "trade" is
    # specifically a buy/sell of a security — dividends/interest carry a symbol
    # too but are income, not trades, so key off transactionType, not the symbol.
    _CATEGORY = {
        "Bought": "trade", "Sold": "trade",
        "Dividend": "dividend", "Qualified Dividend": "dividend",
        "Interest": "interest", "Interest Income": "interest",
        "Margin Interest": "interest",
        "Transfer": "cash", "Online Transfer": "cash",
        "Adjustment": "adjustment", "MISC": "other",
    }
    df["category"] = df["transactionType"].map(_CATEGORY).fillna("other")
    df["is_trade"] = df["category"].eq("trade")
    return df


def trades(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Buy/sell rows only."""
    df = load() if df is None else df
    return df[df["is_trade"]].copy()


def cash(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Everything that isn't a buy/sell (dividends, interest, transfers…)."""
    df = load() if df is None else df
    return df[~df["is_trade"]].copy()


if __name__ == "__main__":
    df = load()
    print(f"{len(df)} rows, {df['transactionDate'].min():%Y-%m-%d} -> "
          f"{df['transactionDate'].max():%Y-%m-%d}")
    print(f"  trades: {df['is_trade'].sum()}   cash moves: {(~df['is_trade']).sum()}")
    print("\nby type:\n" + df["transactionType"].value_counts().to_string())
