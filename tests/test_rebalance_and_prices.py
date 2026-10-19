"""rebalance.py weights and price_history.py windows."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import etrade
import price_history as ph
import rebalance
from conftest import PF_HEADER, write_csv


def _pf(tmp_path, rows):
    return write_csv(tmp_path / "pf.csv", PF_HEADER, rows)


def _targets(tmp_path, **cfg):
    p = tmp_path / "targets.json"
    p.write_text(json.dumps(cfg))
    return p


def test_a_symbol_held_in_two_accounts_is_summed_not_last_row_wins(tmp_path):
    rows = ["A,VTI,ETF,50,250,12500,300,15000,2500,20,50,,,,,2024-01-01",
            "B,VTI,ETF,50,250,12500,300,15000,2500,20,50,,,,,2024-01-01"]
    t = _targets(tmp_path, targets={"VTI": 1.0})
    out, s = rebalance.plan(_pf(tmp_path, rows), t)
    row = out.iloc[0]
    assert s["investable"] == 30000
    assert row["cur_$"] == 30000 and row["delta_$"] == 0 and row["action"] == "hold"


def test_cur_pct_and_tgt_pct_share_a_denominator(tmp_path):
    # 20k held, 10k cash, 5k reserve -> investable 25k. A 40% target is 10k;
    # a name at exactly 10k must read 40.0 next to 40.0, with delta 0.
    rows = ["A,AAPL,EQ,50,200,10000,200,10000,0,0,50,,,,,2024-01-01",
            "A,KO,EQ,100,100,10000,100,10000,0,0,50,,,,,2024-01-01"]
    t = _targets(tmp_path, targets={"AAPL": 0.4, "KO": 0.4}, cash_on_hand=10000, cash_reserve=5000)
    out, _ = rebalance.plan(_pf(tmp_path, rows), t)
    for _, r in out.iterrows():
        assert r["cur_%"] == r["tgt_%"] == 40.0 and r["delta_$"] == 0


def test_percent_targets_are_rejected(tmp_path):
    t = _targets(tmp_path, targets={"AAPL": 20, "KO": 30})
    with pytest.raises(SystemExit, match="over 100%"):
        rebalance.plan(_pf(tmp_path, ["A,AAPL,EQ,1,1,1,1,1,0,0,100,,,,,2024-01-01"]), t)


def test_trading_days_skip_weekends_and_listed_holidays():
    days = rebalance.trading_days("2026-06-18", 3, {"2026-06-19"})
    assert [d.strftime("%Y-%m-%d") for d in days] == ["2026-06-18", "2026-06-22", "2026-06-23"]


def test_ytd_is_measured_from_the_prior_year_end_close():
    bars = [(dt.date(2025, 12, 30), 100.0), (dt.date(2025, 12, 31), 100.0),
            (dt.date(2026, 1, 2), 110.0), (dt.date(2026, 1, 5), 121.0)]
    w = ph.windows(bars)
    assert round(w["ytd"], 6) == 21.0          # was 10.0: measured off Jan 2's close


def test_ytd_falls_back_to_the_first_bar_when_no_prior_year_exists():
    bars = [(dt.date(2026, 1, 2), 110.0), (dt.date(2026, 1, 5), 121.0)]
    assert round(ph.windows(bars)["ytd"], 6) == 10.0


def test_seasonality_is_month_end_to_month_end():
    bars = [(dt.date(2026, 1, 30), 100.0), (dt.date(2026, 1, 31), 110.0),
            (dt.date(2026, 2, 27), 121.0), (dt.date(2026, 3, 31), 121.0)]
    s = ph.seasonality(bars)
    assert round(s[2][0], 6) == 10.0 and s[3] == [0.0] and 1 not in s
