"""gate_check.py: the reconciler must render on the zero-order state, count
only shares still working, and read dated exits as whole days."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

import gate_check as gc
from conftest import OD_HEADER, PF_HEADER, order_row, write_csv

PF_ROW = "EXAMPLE,ACME,EQ,1000,80.00,80000.00,83.39,83390.00,3390.00,4.24,55.00,13,60.5,90.97,-0.1,2026-08-20"


def _frames(tmp_path, order_rows, orders_text=None):
    pf = write_csv(tmp_path / "portfolio.csv", PF_HEADER, [PF_ROW])
    od = tmp_path / "orders.csv"
    if orders_text is not None:
        od.write_text(orders_text)
    else:
        write_csv(od, OD_HEADER, order_rows)
    return gc.load_frames(pf, od, max_age_hours=1e9)


def test_a_bare_newline_orders_file_is_an_empty_book_not_a_crash(tmp_path):
    # What fetch_orders.py wrote for zero orders: one byte, not a CSV.
    pf, od, stale = _frames(tmp_path, [], orders_text="\n")
    assert od.empty and not stale
    out = gc.tier1(pf, od, cap_pct=28.0)
    assert ("WARN", "Zero resting orders at the broker. If the journal names any live "
                    "price gate, it is on paper only.") in out
    assert any(sev == "CRIT" and "NO resting stop" in msg for sev, msg in out)


def test_a_header_only_orders_file_is_also_empty(tmp_path):
    pf, od, _ = _frames(tmp_path, [])
    assert od.empty and list(od.columns)[:2] == ["_accountId", "orderId"]
    assert any(sev == "WARN" and "Zero resting" in msg for sev, msg in gc.tier1(pf, od, 28.0))


def test_a_partial_fill_counts_only_the_shares_still_working(tmp_path):
    # 1000 held. Stop for 1000 ordered with 300 already filled covers 700, and
    # the limit for 1000 with 500 filled has 500 resting. Open-qty sells = 1200.
    rows = [order_row(1, "PARTIAL", "GOOD_UNTIL_CANCEL", "STOP", "ACME", "SELL", 1000, filled=300, stop=75.0),
            order_row(2, "PARTIAL", "GOOD_UNTIL_CANCEL", "LIMIT", "ACME", "SELL", 1000, filled=500, limit=88.0)]
    pf, od, _ = _frames(tmp_path, rows)
    out = dict(gc.tier1(pf, od, 28.0))
    msgs = [m for _, m in gc.tier1(pf, od, 28.0)]
    assert any("stop covers 700 of 1,000 sh (300 sh naked)" in m for m in msgs), msgs
    assert any("1,200 sh of resting sells against 1,000 sh held" in m for m in msgs), msgs
    assert not any("2,000" in m for m in msgs)


def test_full_cover_is_ok_and_good_for_day_is_critical(tmp_path):
    rows = [order_row(1, "OPEN", "GOOD_FOR_DAY", "STOP", "ACME", "SELL", 1000, stop=75.0)]
    pf, od, _ = _frames(tmp_path, rows)
    out = gc.tier1(pf, od, 28.0)
    assert ("OK", "ACME 55.00% of book — stop covers 1,000 sh.") in out
    assert any(sev == "CRIT" and "GOOD_FOR_DAY" in msg for sev, msg in out)


def test_dated_exit_counts_whole_days():
    level = {"id": "T1", "symbol": "ACME", "kind": "DATE", "qty": None, "price": None,
             "label": "DATE", "date": "2026-09-16"}
    od = pd.DataFrame()
    # The afternoon before: ONE day out, a warning -- not "0 day(s)", critical.
    out = gc.tier2([level], [], od, datetime(2026, 9, 15, 14, 0), {"ACME"})
    assert out[0][0] == "WARN" and "1 day(s) out" in out[0][1]
    # The day itself at any hour: zero days, critical -- not "-1 day(s)".
    out = gc.tier2([level], [], od, datetime(2026, 9, 16, 9, 30), {"ACME"})
    assert out[0][0] == "CRIT" and "0 day(s) out" in out[0][1]


def test_template_register_parses_and_its_placeholders_read_as_holes():
    levels, holes = gc.parse_journal_levels((Path(__file__).parents[1] / "JOURNAL.template.md").read_text())
    assert [l["id"] for l in levels] == ["C1", "C2"]
    assert any("ACTIVE TACTICAL TRADE" in h for _, h in holes)


def test_a_struck_through_fired_tranche_is_not_a_live_level():
    # The real row after T2a filled: the first tranche struck out, the second
    # still live. The parser used to report the $88 leg PAPER ONLY.
    text = """## 🔴 ACTIVE TACTICAL TRADE — 100% ACME CONCENTRATION

| Gate | What | Level |
|---|---|---|
| **T2** | **TAKE PROFIT** | ~~500 sh at $88.00~~ **T2a FIRED 2026-09-01 — sold 500 @ $87.58**, then **500 sh at $90.50** still live |
| **T3** | **WRONG** | **$75.00** (−6.25% from the actual blended $80.00) |

---
"""
    levels, holes = gc.parse_journal_levels(text)
    t2 = [l for l in levels if l["id"] == "T2"]
    assert [(l["qty"], l["price"]) for l in t2] == [(500.0, 90.5)]
    assert not any(h for h in holes if "T2" in h[1])
