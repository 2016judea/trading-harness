"""load.py + realized_pnl.py: dates on the exchange clock, no double-counted
page-boundary rows, holding periods in calendar days, fees in the P&L."""
from __future__ import annotations

import pandas as pd

from conftest import TX_HEADER, tx, write_csv
from load import load
from realized_pnl import realized, summary

# 2025-12-02 00:50:19 UTC == 2025-12-01 19:50:19 ET: an after-hours sell.
AFTER_HOURS_SELL_MS = 1764636619000
# 2026-05-28 18:28:34 UTC (14:28 ET) and 2026-05-29 13:31:00 UTC (09:31 ET).
BUY_MS, NEXT_MORNING_MS = 1780079314000, 1780147860000


def test_dates_are_read_on_the_exchange_clock_not_utc(tmp_path):
    p = write_csv(tmp_path / "t.csv", TX_HEADER,
                  [tx(1, AFTER_HOURS_SELL_MS, "Sold", "VT", -10, 100.0)])
    df = load(p)
    d = df.loc[0, "transactionDate"]
    assert str(d.tz) == "America/New_York"
    assert d.strftime("%Y-%m-%d %H:%M") == "2025-12-01 19:50"


def test_a_row_repeated_at_a_page_boundary_is_loaded_once(tmp_path):
    # The API's marker is inclusive, so the last row of page N is the first of
    # page N+1. A pull before fetch_transactions.py dropped them carried six.
    row = tx(26068500463133, BUY_MS, "Bought", "FCX", 5, 58.7469)
    p = write_csv(tmp_path / "t.csv", TX_HEADER, [row, row])
    df = load(p)
    assert len(df) == 1
    assert df["brokerage.quantity"].sum() == 5


def test_a_duplicated_buy_is_not_a_second_lot(tmp_path):
    row = tx(1, BUY_MS, "Bought", "FCX", 5, 50.0)
    sell = tx(2, NEXT_MORNING_MS, "Sold", "FCX", -5, 60.0)
    closed, orphans = realized(load(write_csv(tmp_path / "t.csv", TX_HEADER, [row, row, sell])))
    assert len(closed) == 1 and closed.iloc[0]["qty"] == 5
    assert orphans.empty


def test_holding_period_is_calendar_days_not_truncated_hours(tmp_path):
    # Bought Thursday 14:28 ET, sold Friday 09:31 ET: held one day, not zero.
    rows = [tx(1, BUY_MS, "Bought", "AAPL", 10, 100.0),
            tx(2, NEXT_MORNING_MS, "Sold", "AAPL", -10, 101.0)]
    closed, _ = realized(load(write_csv(tmp_path / "t.csv", TX_HEADER, rows)))
    assert closed.iloc[0]["hold_days"] == 1


def test_fees_come_out_of_the_gain_pro_rata(tmp_path):
    # 10 bought with a $1 commission, sold in two sells of 5 each carrying $0.50.
    rows = [tx(1, BUY_MS, "Bought", "KO", 10, 50.0, fee=1.0),
            tx(2, NEXT_MORNING_MS, "Sold", "KO", -5, 60.0, fee=0.5),
            tx(3, NEXT_MORNING_MS + 60_000, "Sold", "KO", -5, 60.0, fee=0.5)]
    closed, _ = realized(load(write_csv(tmp_path / "t.csv", TX_HEADER, rows)))
    # Gross 10 * $10 = $100; fees $1 + $0.50 + $0.50 = $2.
    assert round(closed["gain"].sum(), 6) == 98.0
    assert round(closed.iloc[0]["gain"], 6) == 49.0
    assert round(closed.iloc[0]["return_pct"], 6) == round(49.0 / 250.0 * 100, 6)


def test_fifo_partial_fill_and_pre_window_sell(tmp_path):
    rows = [tx(1, BUY_MS, "Bought", "X", 100, 10.0),
            tx(2, BUY_MS + 1_000, "Bought", "X", 100, 12.0),
            tx(3, NEXT_MORNING_MS, "Sold", "X", -150, 15.0),
            tx(4, NEXT_MORNING_MS + 1_000, "Sold", "X", -100, 15.0)]
    closed, orphans = realized(load(write_csv(tmp_path / "t.csv", TX_HEADER, rows)))
    assert closed["qty"].tolist() == [100, 50, 50]
    assert closed["buy_price"].tolist() == [10.0, 12.0, 12.0]
    assert orphans.iloc[0]["qty"] == 50          # sold more than the window bought
    st = summary(closed, orphans)
    assert st["lots"] == 3 and st["excluded_orphan_sells"] == 1
    assert st["net"] == 100 * 5 + 50 * 3 + 50 * 3


def test_same_second_rows_keep_api_order(tmp_path):
    # Two sells in one second at different prices: the file's order is the
    # API's order, and a quicksort may not keep it.
    rows = [tx(1, BUY_MS, "Bought", "F", 100, 10.0)]
    rows += [tx(10 + i, NEXT_MORNING_MS, "Sold", "F", -10, 11.0 + i / 100) for i in range(30)]
    df = load(write_csv(tmp_path / "t.csv", TX_HEADER, rows))
    sells = df[df.transactionType == "Sold"]
    assert sells["transactionId"].tolist() == list(range(10, 40))
    assert pd.api.types.is_datetime64_any_dtype(df["transactionDate"])
