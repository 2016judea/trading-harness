#!/usr/bin/env python3
"""Rank a set of businesses on the Buffett/Munger filters, side by side.

    python3 compare.py --bond 5.19 CTVA=82.81 CEG=273.43 NVDA=208.48

The ranking metric is the EXPECTATIONS GAP: the owner-earnings growth the
price requires, minus the growth the business has actually delivered. Negative
is good -- you are being asked to pay for less than the company already does.

This is the only comparison that survives calibration. Absolute "fair values"
move with the hurdle rate; the gap between required and delivered does not.
"""
import argparse, sys, os
import sec_financials as sf
from owner_earnings import build, owner_earnings, refusal, latest_shares

M = 1e6


def dcf(base, g, r, tg, n=10):
    pv, cf = 0.0, base
    for i in range(1, n + 1):
        cf *= (1 + g)
        pv += cf / (1 + r) ** i
    return pv + (cf * (1 + tg) / (r - tg)) / (1 + r) ** n


def implied_growth(base, mcap, r, tg=0.025):
    if base <= 0:
        return None
    lo, hi = -0.30, 0.80
    for _ in range(200):
        mid = (lo + hi) / 2
        if dcf(base, mid, r, min(tg, r - 0.005)) < mcap:
            lo = mid
        else:
            hi = mid
    return mid


def cagr(a, b, n):
    if a is None or b is None or a <= 0 or b <= 0 or n <= 0:
        return None
    return (b / a) ** (1.0 / n) - 1


def choose_base(oe, fs):
    """Cyclical -> average. Compounder -> latest. Say which, and why.

    Averaging five years is correct for a cyclical whose good years fund its
    bad ones. It is WRONG for a compounder (it averages in years the company
    has permanently outgrown) and wrong for a spin-off (it averages in
    carve-out losses that belong to the former parent). Discovered 2026-08-24
    running this on CEG/GEV/NVDA -- the average made NVDA look like it needed
    25% growth and CEG like it needed 28%, both artefacts.
    """
    w = [oe[f] for f in fs][-5:]
    ups = sum(1 for i in range(1, len(w)) if w[i] > w[i - 1])
    n = len(w) - 1
    if n >= 2 and ups >= n - 0 and min(w) > 0:
        return w[-1], "latest (monotonic riser)"
    if n >= 3 and ups >= n - 1 and min(w) > 0:
        return w[-1], "latest (rising %d of %d)" % (ups, n)
    if min(w) <= 0:
        pos = [x for x in w if x > 0]
        # Munger keeps three baskets: in, out, and TOO HARD. Fewer than three
        # positive years in five is not a cheap stock, it is an absence of the
        # thing being valued. Averaging the winners is data-mining.
        if len(pos) < 3:
            return None, ("only %d of %d years had positive owner earnings "
                          "-> TOO HARD, no durable earning power" % (len(pos), len(w)))
        return sum(pos) / len(pos), "avg of POSITIVE yrs only (%d of %d)" % (len(pos), len(w))
    return sum(w) / len(w), "5-yr average (cyclical)"


def analyse(ticker, price, bond, hurdle):
    name, cik, fys, d = build(ticker, 8)
    fx = sf.get(sf.FACTS.format(cik=cik))["facts"]
    sh = latest_shares(fx)
    oe = {f: owner_earnings(d, f)[0] for f in fys}
    oe = {f: v for f, v in oe.items() if v is not None}
    if not oe:
        return {"ticker": ticker, "name": name, "error": "no owner earnings computable"}
    fs = sorted(oe)
    last = fs[-1]
    hist = [oe[f] for f in fs][-5:]
    norm, basis = choose_base(oe, fs)
    if norm is None:
        return {"ticker": ticker, "name": name, "error": basis}
    mcap = price * sh
    eq = d["equity"].get(last) or 0
    tang = eq - (d["goodwill"].get(last) or 0) - (d["intangibles"].get(last) or 0)
    win = fs[-5:]
    f0 = win[0]
    for cand in win[:-1]:
        if oe.get(cand, 0) > 0:
            f0 = cand
            break
    span = last - f0
    r0, r1 = d["revenue"].get(f0), d["revenue"].get(last)
    g0, g1 = d["gross_profit"].get(f0), d["gross_profit"].get(last)
    dep = d["d_and_a"].get(last) or 0
    cpx = d["capex"].get(last) or 0
    # SANITY: owner earnings must roughly track the cash the business throws off.
    # A big positive OE against weak/negative FCF means the D&A add-back is
    # carrying a goodwill IMPAIRMENT, not depreciation -- adding back a write-off
    # of money already lost is how you value a melting business at a premium.
    # Grounded 2026-08-24: FMC screened at a 57% owner-earnings yield on a -108%
    # ROE, and KHC at 7.3% on -14% ROE. Both were impairments.
    fcf_w, oe_w = [], []
    for f in fs[-5:]:
        c, k = d["cfo"].get(f), d["capex"].get(f)
        if c is not None and k is not None:
            fcf_w.append(c - k)
            oe_w.append(oe[f])
    oe_fcf = None
    if fcf_w and sum(fcf_w) != 0:
        oe_fcf = sum(oe_w) / sum(fcf_w)
    return {
        "ticker": ticker, "name": name, "price": price, "mcap": mcap,
        "fy_first": fs[0], "fy_last": last, "n_years": len(fs),
        "oe_last": oe[last], "oe_norm": norm,
        "oe_yield": norm / mcap, "oe_yield_last": oe[last] / mcap,
        "roe": (d["net_income"].get(last) / eq) if eq else None,
        "rotc": (oe[last] / tang) if tang > 0 else None,
        "gm_first": (g0 / r0) if (g0 and r0) else None,
        "gm_last": (g1 / r1) if (g1 and r1) else None,
        "rev_cagr": cagr(r0, r1, span),
        "oe_cagr": cagr(oe.get(f0), oe[last], span),
        "basis": basis, "cagr_from": f0,
        "capex_over_da": (cpx / dep) if dep else None,
        "oe_over_fcf": oe_fcf,
        "ni_last": d["net_income"].get(last),
        "oe_window": hist,
        "implied": implied_growth(norm, mcap, hurdle / 100.0),
        "bond": bond / 100.0,
    }


def verdict(r):
    """(code, label) for one analysed row. Refusals come first and replace the
    gap reading; they never sit beside it.

    Before 2026-11-20 a row whose delivered growth couldn't be computed (owner
    earnings <= 0 at either end of the window) was dropped from the verdict
    table without a word. FMC and KHC, the two impairment cases the refusals
    exist for, were exactly those rows: they vanished instead of being refused.
    """
    oe_window = r.get("oe_window") or []
    why = refusal(r.get("ni_last"), oe_window, r.get("oe_over_fcf")) if oe_window else None
    if why:
        return "refused", why
    if r["implied"] is None:
        return "no_reading", "no implied growth: normalised owner earnings <= 0"
    if r["oe_cagr"] is None:
        return "no_reading", ("no delivered growth: owner earnings <= 0 at the start "
                              "or end of the window")
    gap = r["implied"] - r["oe_cagr"]
    if r["oe_cagr"] > 0.30:
        return "do_not_extrapolate", "delivered CAGR >30% -- DO NOT EXTRAPOLATE"
    if r["capex_over_da"] and r["capex_over_da"] > 1.15:
        return "growth_capex", ("capex %.2fx D&A -- owner earnings understated, growth capex"
                                % r["capex_over_da"])
    return (("below_record", "CHEAP vs its own record") if gap < 0 else
            ("improvement", "priced for improvement") if gap < 0.05 else
            ("a_lot", "priced for a lot") if gap < 0.12 else
            ("heroics", "priced for heroics"))


def screen(pairs, bond, hurdle=8.0):
    """[(ticker, price)] -> (rows, excluded), rows in the order given."""
    from concurrent.futures import ThreadPoolExecutor

    def one(pair):
        t, px = pair
        try:
            return analyse(t.upper(), float(px), bond, hurdle)
        except Exception as e:
            return {"ticker": t.upper(), "error": str(e)[:60]}

    # Each name is its own multi-MB SEC pull; four at a time stays well under
    # SEC's 10 requests/second and turns a cold 8-name screen from ~16s into ~4s.
    with ThreadPoolExecutor(max_workers=4) as ex:
        done = list(ex.map(one, pairs))
    rows = [r for r in done if "error" not in r]
    bad = [r for r in done if "error" in r]
    for r in rows:
        r["verdict"], r["verdict_label"] = verdict(r)
        r["gap"] = (r["implied"] - r["oe_cagr"]
                    if r["implied"] is not None and r["oe_cagr"] is not None else None)
    return rows, bad


def by_gap(rows):
    """Most negative gap first (paying for less than it already does); no reading last."""
    return sorted(rows, key=lambda r: (r["gap"] is None, r["gap"] if r["gap"] is not None else 0))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("pairs", nargs="+", help="TICKER=PRICE")
    p.add_argument("--bond", type=float, required=True)
    p.add_argument("--hurdle", type=float, default=8.0)
    a = p.parse_args()

    pairs = []
    for pr in a.pairs:
        t, _, px = pr.partition("=")
        pairs.append((t, px))
    ok, bad = screen(pairs, a.bond, a.hurdle)

    pct = lambda v: "   n/a" if v is None else "%5.1f%%" % (100 * v)
    print("=" * 118)
    print("BUFFETT / MUNGER SCREEN   long bond %.2f%%   required return %.0f%%"
          % (a.bond, a.hurdle))
    print("=" * 118)
    print("%-6s %8s %9s %8s %8s %8s %8s %8s %8s %9s %9s"
          % ("", "price", "mktcap$B", "OE yld", "ROTC", "ROE",
             "GM 1st", "GM last", "rev cagr", "OE cagr", "implied g"))
    print("-" * 118)
    for r in sorted(ok, key=lambda x: -(x["oe_yield"])):
        print("%-6s %8.2f %9.1f %8s %8s %8s %8s %8s %8s %9s %9s"
              % (r["ticker"], r["price"], r["mcap"] / 1e9, pct(r["oe_yield"]),
                 pct(r["rotc"]), pct(r["roe"]), pct(r["gm_first"]), pct(r["gm_last"]),
                 pct(r["rev_cagr"]), pct(r["oe_cagr"]), pct(r["implied"])))

    print("\n%-6s %-10s %-12s %-12s   %s" % ("", "implied g", "delivered g", "GAP", "verdict"))
    print("-" * 118)
    for r in by_gap(ok):
        gap = "%+11.1fpt" % (100 * r["gap"]) if r["gap"] is not None else "%11s  " % "n/a"
        print("%-6s %-10s %-12s %s   %s"
              % (r["ticker"], pct(r["implied"]), pct(r["oe_cagr"]), gap, r["verdict_label"]))

    print("\n  base used for owner earnings:")
    for r in ok:
        print("     %-6s %-34s  FY%d-FY%d   capex/D&A %s"
              % (r["ticker"], r["basis"], r["cagr_from"], r["fy_last"],
                 "n/a" if r["capex_over_da"] is None else "%.2fx" % r["capex_over_da"]))
    print("\n  GAP = growth the PRICE requires minus growth the BUSINESS delivered.")
    print("  Negative = you are paying for less than it already does.")
    print("  ** Short histories lie: check n_years before trusting a CAGR. **")
    for r in ok:
        if r["n_years"] < 5:
            print("     %s has only %d fiscal years (spin-off/IPO) -- CAGR is fragile"
                  % (r["ticker"], r["n_years"]))
    for r in bad:
        print("     %-6s EXCLUDED: %s" % (r["ticker"], r["error"]))
    print("=" * 118)


if __name__ == "__main__":
    main()
