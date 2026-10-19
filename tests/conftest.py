"""Shared fixtures. The one rule: NO test may reach E*TRADE.

Every module under test imports `etrade`, whose `session()` is the only door to
the network (and to the interactive OAuth prompt). The autouse fixture below
replaces it with a tripwire, so a test that forgets to stub `etrade.get` fails
loudly instead of previewing, placing or re-authing anything for real.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import etrade  # noqa: E402  (after sys.path)


@pytest.fixture(autouse=True)
def no_live_etrade(monkeypatch):
    def tripwire(*a, **k):
        raise AssertionError("a test tried to open a live E*TRADE session")

    monkeypatch.setattr(etrade, "session", tripwire)
    monkeypatch.setattr(etrade, "_interactive_authorize", tripwire)
    yield


def write_csv(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


TX_HEADER = ("transactionId,accountId,_accountId,transactionDate,postDate,amount,"
             "transactionType,description,brokerage.displaySymbol,brokerage.quantity,"
             "brokerage.price,brokerage.fee,brokerage.settlementDate")


def tx(tid, ts_ms, ttype, sym, qty, price, fee=0.0, amount=None):
    """One transactions.csv row in E*TRADE's shape (epoch ms, signed sell qty)."""
    amount = amount if amount is not None else -qty * price
    return (f"{tid},EXAMPLE,EXAMPLE,{ts_ms},{ts_ms},{amount},{ttype},{ttype} {sym},"
            f"{sym},{qty},{price},{fee},{ts_ms}")


PF_HEADER = ("_accountId,symbol,securityType,quantity,costPerShare,totalCost,lastPrice,"
             "marketValue,totalGain,totalGainPct,pctOfPortfolio,perform12Month,"
             "week52Low,week52High,beta,dateAcquired")

OD_HEADER = ("_accountId,orderId,orderType,status,orderTerm,priceType,symbol,securityType,"
             "orderAction,orderedQuantity,filledQuantity,limitPrice,stopPrice,stopLimitPrice,"
             "averageExecutionPrice,placedTime,executedTime,orderValue,allOrNone,"
             "marketSession,replacesOrderId,replacedByOrderId")


def order_row(oid, status, term, ptype, sym, action, qty, filled=0, limit="", stop=""):
    return (f"EXAMPLE,{oid},EQ,{status},{term},{ptype},{sym},EQ,{action},{qty},{filled},"
            f"{limit},{stop},,,2026-08-01 09:35:00,,,False,REGULAR,,")
