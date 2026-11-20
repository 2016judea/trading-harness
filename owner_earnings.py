#!/usr/bin/env python3
"""Owner earnings and intrinsic value, the Buffett/Munger way.

    python3 owner_earnings.py CTVA --price 82.33 --bond 5.19

Deliberately simple. Buffett has never published a DCF; he capitalises owner
earnings against the long bond and then demands a margin of safety instead of
inventing a risk premium. This prints the whole ladder so the assumption doing
the work is always visible -- there is no single number to hide behind.

Pulls its inputs from ../sec-financials/sec_financials.py (as-reported XBRL),
so no figure here is typed by hand.
"""
import argparse, sys, os
import sec_financials as sf

M = 1e6


def money(v):
    if v is None: return "     n/a"
    return "%8.0f" % (v / M) if abs(v) < 1e10 else "%8.1f" % (v / M)


def build(ticker, years=7):
    cik, name = sf.resolve_cik(ticker)
    fx = sf.get(sf.FACTS.format(cik=cik))["facts"]
    get = lambda tags, flow: {fy: v[0] for fy, v in sf.annual(fx, tags, flow).items()}
    d = {k: get(sf.LINES[k], k in sf.FLOWS) for k in sf.LINES}
    # split D&A -- amortisation of ACQUIRED intangibles is a non-cash charge with
    # no replacement cost when the company separately expenses its own R&D.
    d["depreciation"] = get(["Depreciation"], True)
    d["amortization"] = get(["AmortizationOfIntangibleAssets"], True)
    d["restructuring"] = get(["RestructuringCharges"], True)
    fys = sorted(set(d["revenue"]) & set(d["net_income"]))[-years:]
    return name, cik, fys, d


def owner_earnings(d, fy):
    """Buffett, 1986 Berkshire letter, Appendix:
       owner earnings = reported earnings
                      + depreciation, depletion, amortisation, other non-cash
                      - average annual MAINTENANCE capex
       (and, where needed, additional working capital.)"""
    ni = d["net_income"].get(fy)
    dep = d["depreciation"].get(fy)
    amt = d["amortization"].get(fy)
    da = (dep + amt) if (dep is not None and amt is not None) else d["d_and_a"].get(fy)
    cap = d["capex"].get(fy)
    if None in (ni, da, cap):
        return None, None
    # Maintenance capex: total capex is the conservative choice for a business
    # whose asset base is not growing. If capex >> depreciation the split must
    # be made by hand -- the excess is GROWTH capex and belongs to the owner.
    return ni + da - cap, (ni, dep, amt, da, cap)


def dcf(base, g, r, tg, n=10):
    pv, cf = 0.0, base
    for i in range(1, n + 1):
        cf *= (1 + g)
        pv += cf / (1 + r) ** i
    term = cf * (1 + tg) / (r - tg)
    return pv + term / (1 + r) ** n


def refusal(ni_last, oe_window, oe_over_fcf):
    """Munger's third basket. Returns the reason this is TOO HARD, or None.

    The README promises these three, and compare.py's verdicts use the same
    function, so the screen and the single-name valuation can't disagree:
      * fewer than three positive owner-earnings years in five: there is no
        durable earning power to value, and averaging the winners is data-mining;
      * a loss in the latest year;
      * owner earnings >1.6x actual free cash flow (or FCF negative against
        positive owner earnings): the D&A add-back is carrying an impairment,
        not depreciation. FMC screened at a 57% owner-earnings yield on a -108%
        ROE, and KHC at 7.3% on -14% ROE (2026-08-24). Both were impairments.
    """
    pos = [x for x in oe_window if x is not None and x > 0]
    if len(pos) < 3:
        return ("only %d of %d years had positive owner earnings "
                "-> TOO HARD, no durable earning power" % (len(pos), len(oe_window)))
    if ni_last is not None and ni_last < 0:
        return "LOSS-MAKING in the latest year -- TOO HARD"
    if oe_over_fcf and (oe_over_fcf > 1.6 or oe_over_fcf < 0):
        return ("owner earnings %.1fx actual FCF -- add-back is not cash, REFUSE"
                % oe_over_fcf)
    return None


def latest_shares(fx):
    """Latest dei:EntityCommonStockSharesOutstanding, by filing date."""
    return sorted(fx["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"],
                  key=lambda x: x.get("filed", ""))[-1]["val"]


def valuation(ticker, price=None, bond=None, shares=None, growth=4.0, hurdle=10.0,
              terminal=2.5, years=7):
    """Everything main() prints, as a dict. `shares` in millions, like --shares.

    With no price only the owner-earnings history, return on capital and the
    refusal are computed: what the market is asking needs a price, and this
    never guesses one. A refusal stops it after the history, because a value
    beside a refusal reads as a value.
    """
    name, cik, fys, d = build(ticker, years)
    fx = sf.get(sf.FACTS.format(cik=cik))["facts"]
    out = {"ticker": ticker.upper(), "name": name, "cik": cik, "fiscal_years": fys,
           "price": price, "bond": bond, "growth": growth, "hurdle": hurdle,
           "terminal": terminal}
    rows = {k: {f: d[k].get(f) for f in fys}
            for k in ("net_income", "depreciation", "amortization", "capex",
                      "restructuring", "cfo", "d_and_a")}
    oe = {}
    for f in fys:
        v, _ = owner_earnings(d, f)
        oe[f] = v
    fcf = {f: (d["cfo"].get(f) - d["capex"].get(f))
           for f in fys if d["cfo"].get(f) and d["capex"].get(f)}
    out.update(rows=rows, owner_earnings=oe, fcf=fcf)

    have = [oe[f] for f in fys if oe.get(f) is not None]
    have5 = have[-5:]
    fcf5 = [fcf[f] for f in fys if f in fcf][-5:]
    latest = fys[-1] if fys else None
    out.update(latest=latest,
               norm_oe=sum(have5) / len(have5) if have5 else None, n_norm_oe=len(have5),
               norm_fcf=sum(fcf5) / len(fcf5) if fcf5 else None, n_norm_fcf=len(fcf5))

    oe_w, fcf_w = [], []
    for f in [f for f in fys if oe.get(f) is not None][-5:]:
        c, k = d["cfo"].get(f), d["capex"].get(f)
        if c is not None and k is not None:
            fcf_w.append(c - k)
            oe_w.append(oe[f])
    out["oe_over_fcf"] = (sum(oe_w) / sum(fcf_w)) if fcf_w and sum(fcf_w) != 0 else None
    out["refusal"] = (refusal(d["net_income"].get(latest), have5, out["oe_over_fcf"])
                      if latest is not None and oe.get(latest) is not None
                      else "no owner earnings computable for the latest year "
                           "-> TOO HARD")
    dep, cpx = d["d_and_a"].get(latest) or 0, d["capex"].get(latest) or 0
    out["capex_over_da"] = (cpx / dep) if dep else None
    if out["refusal"]:
        return out

    norm_oe = out["norm_oe"]
    eq = d["equity"].get(latest)
    gw = d["goodwill"].get(latest) or 0
    it = d["intangibles"].get(latest) or 0
    ni = d["net_income"].get(latest)
    tang = (eq - gw - it) if eq is not None else None
    out["capital"] = {
        "equity": eq, "goodwill": gw, "intangibles": it, "tangible": tang,
        "roe": (ni / eq) if (eq and ni is not None) else None,
        "oe_on_equity": (oe[latest] / eq) if eq else None,
        "rotc": (oe[latest] / tang) if (tang is not None and tang > 0) else None,
    }
    if price is None:
        return out

    if shares:
        sh = shares * M
    else:
        sh = latest_shares(fx)
    cash = d["cash"].get(latest, 0)
    debt = (d["lt_debt"].get(latest) or 0) + (d["st_debt"].get(latest) or 0)
    mcap = price * sh
    ev = mcap + debt - cash
    out["market"] = {"shares": sh, "mcap": mcap, "debt": debt, "cash": cash, "ev": ev,
                     "yields": [("normalised owner earnings", norm_oe / mcap),
                                ("normalised FCF", out["norm_fcf"] / mcap
                                 if out["norm_fcf"] is not None else None),
                                ("latest FY owner earnings", oe[latest] / mcap)]}

    g, r, tg = growth / 100, hurdle / 100, terminal / 100
    b = bond / 100

    def per_share(v): return v / sh

    anchors = [
        ("(a) bond-equivalent: normalised OE capitalised at the %.2f%% long bond,"
         % bond, "no growth", norm_oe / b),
        ("(b) two-stage DCF on normalised OE: %.1f%% for 10y, %.1f%% terminal,"
         % (growth, terminal), "%.0f%% required return" % hurdle, dcf(norm_oe, g, r, tg)),
        ("(c) same DCF on the LATEST year's owner earnings", "(peak-cycle)",
         dcf(oe[latest], g, r, tg)),
    ]
    out["anchors"] = [{"label": h, "basis": t, "value": v, "per_share": per_share(v),
                       "price_vs_value_pct": 100 * (price / per_share(v) - 1)}
                      for h, t, v in anchors]
    mid = sorted(per_share(x[2]) for x in anchors)[1]
    out["midpoint_per_share"] = mid
    out["margin_of_safety"] = [{"mos_pct": mos, "buy_below": mid * (1 - mos / 100),
                                "market_above": price > mid * (1 - mos / 100)}
                               for mos in (0, 25, 33)]

    rev = []
    for h in (8, 9, 10, 12):
        rr = h / 100.0
        lo, hi = -0.20, 0.60
        for _ in range(200):
            midg = (lo + hi) / 2
            if dcf(norm_oe, midg, rr, min(tg, rr - 0.005)) < mcap:
                lo = midg
            else:
                hi = midg
        rev.append({"required_return_pct": h, "implied_growth": midg})
    out["reverse_dcf"] = rev

    hurdles = [7, 8, 9, 10, 12]
    out["sensitivity"] = {"hurdles_pct": hurdles, "rows": [
        {"growth_pct": gg, "per_share": [
            per_share(dcf(norm_oe, gg / 100.0, h / 100.0, min(tg, h / 100.0 - 0.005)))
            for h in hurdles]} for gg in (2, 4, 6, 8, 10)]}
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ticker")
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--bond", type=float, required=True, help="30-yr Treasury %")
    p.add_argument("--shares", type=float, help="millions; default = latest dei")
    p.add_argument("--growth", type=float, default=4.0, help="stage-1 growth %")
    p.add_argument("--hurdle", type=float, default=10.0, help="required return %")
    p.add_argument("--terminal", type=float, default=2.5)
    p.add_argument("--years", type=int, default=7)
    a = p.parse_args()
    try:
        v = valuation(a.ticker, a.price, a.bond, a.shares, a.growth, a.hurdle,
                      a.terminal, a.years)
    except (sf.MissingContact, sf.TickerNotFound) as e:
        sys.exit(str(e))
    report(v)


def report(v):
    fys, rows, oe = v["fiscal_years"], v["rows"], v["owner_earnings"]
    print("=" * 78)
    print("%s (%s)  CIK %d   price $%.2f   long bond %.2f%%"
          % (v["name"], v["ticker"], v["cik"], v["price"], v["bond"]))
    print("=" * 78)

    print("\n1. OWNER EARNINGS  ($M)   net income + D&A - maintenance capex")
    print("   %-22s%s" % ("FY", "".join("%9d" % f for f in fys)))
    for lbl, key in [("net income", "net_income"), ("+ depreciation", "depreciation"),
                     ("+ amortisation", "amortization"), ("- capex", "capex")]:
        print("   %-22s%s" % (lbl, "".join(money(rows[key].get(f)) + " " for f in fys)))
    print("   %-22s%s" % ("= OWNER EARNINGS", "".join(money(oe.get(f)) + " " for f in fys)))
    print("   %-22s%s" % ("(cross-check: FCF)", "".join(money(v["fcf"].get(f)) + " " for f in fys)))
    print("   %-22s%s" % ("restructuring chg", "".join(money(rows["restructuring"].get(f)) + " " for f in fys)))

    if v["refusal"]:
        print("\n   REFUSED: %s" % v["refusal"])
        print("   No value is printed. A value beside a refusal reads as a value.")
        print("=" * 78)
        return

    latest = v["latest"]
    print("\n   normalised owner earnings (%d-yr avg) : $%.0fM" % (v["n_norm_oe"], v["norm_oe"] / M))
    print("   normalised FCF          (%d-yr avg) : $%.0fM   <- independent road"
          % (v["n_norm_fcf"], v["norm_fcf"] / M))
    print("   latest FY%d owner earnings          : $%.0fM" % (latest, oe[latest] / M))

    c = v["capital"]
    eq, gw, it, tang = c["equity"], c["goodwill"], c["intangibles"], c["tangible"]
    print("\n2. RETURN ON CAPITAL  (Munger's first question)")
    if tang <= 0:
        print("   NOTE: tangible capital is negative -- buybacks have taken book")
        print("   equity below zero. Return on tangible capital is meaningless here;")
        print("   judge the moat on margins and pricing power instead.")
    print("   equity $%.0fM  - goodwill $%.0fM  - intangibles $%.0fM  = tangible $%.0fM"
          % (eq / M, gw / M, it / M, tang / M))
    print("   ROE  (GAAP net income / equity)          : %5.1f%%" % (100 * c["roe"]))
    print("   return on ACCOUNTING capital (OE/equity) : %5.1f%%" % (100 * c["oe_on_equity"]))
    if tang > 0:
        print("   return on TANGIBLE capital   (OE/tang.)  : %5.1f%%" % (100 * c["rotc"]))
    print("   -> the spread between these two IS the price of the acquisition")
    print("      that created the goodwill. A great business bought dearly.")

    m = v["market"]
    print("\n3. WHAT THE MARKET IS ASKING")
    print("   shares out %.1fM   market cap $%.2fB" % (m["shares"] / M, m["mcap"] / 1e9))
    print("   + debt $%.0fM - cash $%.0fM  = enterprise value $%.2fB"
          % (m["debt"] / M, m["cash"] / M, m["ev"] / 1e9))
    for lbl, y in m["yields"]:
        print("   owner-earnings yield on %-26s %5.2f%%   (long bond %.2f%%)"
              % (lbl + ":", 100 * y, v["bond"]))

    print("\n4. INTRINSIC VALUE  (three anchors, deliberately simple)")
    for an in v["anchors"]:
        print("   %s %s" % (an["label"], an["basis"]))
        print("       -> $%.2fB   =  $%.2f per share   (market $%.2f, %+.0f%%)"
              % (an["value"] / 1e9, an["per_share"], v["price"], an["price_vs_value_pct"]))

    print("\n5. MARGIN OF SAFETY  (Graham: buy at a discount TO the estimate)")
    mid = v["midpoint_per_share"]
    print("   midpoint of the three anchors: $%.2f/share" % mid)
    for row in v["margin_of_safety"]:
        print("     buy below %2d%% margin of safety : $%6.2f   %s"
              % (row["mos_pct"], row["buy_below"],
                 "MARKET IS ABOVE THIS" if row["market_above"] else "market qualifies"))
    print("   market price $%.2f  vs  midpoint estimate $%.2f  ->  %+.0f%%"
          % (v["price"], mid, 100 * (v["price"] / mid - 1)))

    # ---- the honest question: not "is my model right" but "what does the
    # price require you to believe?"  Reverse the DCF and solve for growth.
    print("\n6. REVERSE DCF -- what the market price REQUIRES")
    print("   (arguing about my growth assumption is a waste of breath; this")
    print("    asks what growth the price itself is quoting.)")
    for row in v["reverse_dcf"]:
        print("     at a %2d%% required return, price $%.2f implies %5.1f%% owner-earnings"
              " growth for 10 yrs" % (row["required_return_pct"], v["price"],
                                     100 * row["implied_growth"]))

    print("\n7. SENSITIVITY -- value per share (rows: growth, cols: required return)")
    s = v["sensitivity"]
    print("        %s" % "".join("%9d%%" % h for h in s["hurdles_pct"]))
    for row in s["rows"]:
        print("   %2d%%  %s" % (row["growth_pct"], "".join("%10.2f" % x for x in row["per_share"])))
    print("   market price $%.2f -- cells ABOVE it are combinations that justify"
          " the price" % v["price"])
    print("=" * 78)


if __name__ == "__main__":
    main()
