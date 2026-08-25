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

    name, cik, fys, d = build(a.ticker, a.years)
    cik_, _ = sf.resolve_cik(a.ticker)
    fx = sf.get(sf.FACTS.format(cik=cik_))["facts"]
    shares = a.shares * M if a.shares else sorted(
        fx["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"],
        key=lambda x: x.get("filed", ""))[-1]["val"]

    print("=" * 78)
    print("%s (%s)  CIK %d   price $%.2f   long bond %.2f%%"
          % (name, a.ticker.upper(), cik, a.price, a.bond))
    print("=" * 78)

    print("\n1. OWNER EARNINGS  ($M)   net income + D&A - maintenance capex")
    print("   %-22s%s" % ("FY", "".join("%9d" % f for f in fys)))
    rows = {}
    for lbl, key in [("net income", "net_income"), ("+ depreciation", "depreciation"),
                     ("+ amortisation", "amortization"), ("- capex", "capex")]:
        print("   %-22s%s" % (lbl, "".join(money(d[key].get(f)) + " " for f in fys)))
    oe = {}
    for f in fys:
        v, _ = owner_earnings(d, f)
        oe[f] = v
    print("   %-22s%s" % ("= OWNER EARNINGS", "".join(money(oe.get(f)) + " " for f in fys)))
    fcf = {f: (d["cfo"].get(f) - d["capex"].get(f))
           for f in fys if d["cfo"].get(f) and d["capex"].get(f)}
    print("   %-22s%s" % ("(cross-check: FCF)", "".join(money(fcf.get(f)) + " " for f in fys)))
    print("   %-22s%s" % ("restructuring chg", "".join(money(d["restructuring"].get(f)) + " " for f in fys)))

    have = [oe[f] for f in fys if oe.get(f) is not None]
    have5 = have[-5:]
    norm_oe = sum(have5) / len(have5)
    fcf5 = [fcf[f] for f in fys if f in fcf][-5:]
    norm_fcf = sum(fcf5) / len(fcf5)
    latest = fys[-1]

    print("\n   normalised owner earnings (%d-yr avg) : $%.0fM" % (len(have5), norm_oe / M))
    print("   normalised FCF          (%d-yr avg) : $%.0fM   <- independent road"
          % (len(fcf5), norm_fcf / M))
    print("   latest FY%d owner earnings          : $%.0fM" % (latest, oe[latest] / M))

    print("\n2. RETURN ON CAPITAL  (Munger's first question)")
    eq = d["equity"].get(latest); gw = d["goodwill"].get(latest) or 0
    it = d["intangibles"].get(latest) or 0; ni = d["net_income"].get(latest)
    tang = eq - gw - it
    if tang <= 0:
        print("   NOTE: tangible capital is negative -- buybacks have taken book")
        print("   equity below zero. Return on tangible capital is meaningless here;")
        print("   judge the moat on margins and pricing power instead.")
    print("   equity $%.0fM  - goodwill $%.0fM  - intangibles $%.0fM  = tangible $%.0fM"
          % (eq / M, gw / M, it / M, tang / M))
    print("   ROE  (GAAP net income / equity)          : %5.1f%%" % (100 * ni / eq))
    print("   return on ACCOUNTING capital (OE/equity) : %5.1f%%" % (100 * oe[latest] / eq))
    if tang > 0:
        print("   return on TANGIBLE capital   (OE/tang.)  : %5.1f%%" % (100 * oe[latest] / tang))
    print("   -> the spread between these two IS the price of the acquisition")
    print("      that created the goodwill. A great business bought dearly.")

    cash = d["cash"].get(latest, 0)
    debt = (d["lt_debt"].get(latest) or 0) + (d["st_debt"].get(latest) or 0)
    mcap = a.price * shares
    ev = mcap + debt - cash
    print("\n3. WHAT THE MARKET IS ASKING")
    print("   shares out %.1fM   market cap $%.2fB" % (shares / M, mcap / 1e9))
    print("   + debt $%.0fM - cash $%.0fM  = enterprise value $%.2fB"
          % (debt / M, cash / M, ev / 1e9))
    for lbl, v in [("normalised owner earnings", norm_oe),
                   ("normalised FCF", norm_fcf),
                   ("latest FY owner earnings", oe[latest])]:
        print("   owner-earnings yield on %-26s %5.2f%%   (long bond %.2f%%)"
              % (lbl + ":", 100 * v / mcap, a.bond))

    print("\n4. INTRINSIC VALUE  (three anchors, deliberately simple)")
    g, r, tg = a.growth / 100, a.hurdle / 100, a.terminal / 100
    b = a.bond / 100

    def per_share(v): return v / shares

    def dcf(base, g, r, tg, n=10):
        pv, cf = 0.0, base
        for i in range(1, n + 1):
            cf *= (1 + g)
            pv += cf / (1 + r) ** i
        term = cf * (1 + tg) / (r - tg)
        return pv + term / (1 + r) ** n

    anchors = []
    v = norm_oe / b
    anchors.append(("(a) bond-equivalent: normalised OE capitalised at the %.2f%% long bond,"
                    % a.bond, "no growth", v))
    v2 = dcf(norm_oe, g, r, tg)
    anchors.append(("(b) two-stage DCF on normalised OE: %.1f%% for 10y, %.1f%% terminal,"
                    % (a.growth, a.terminal), "%.0f%% required return" % a.hurdle, v2))
    v3 = dcf(oe[latest], g, r, tg)
    anchors.append(("(c) same DCF on the LATEST year's owner earnings", "(peak-cycle)", v3))

    for head, tail, val in anchors:
        print("   %s %s" % (head, tail))
        print("       -> $%.2fB   =  $%.2f per share   (market $%.2f, %+.0f%%)"
              % (val / 1e9, per_share(val), a.price,
                 100 * (a.price / per_share(val) - 1)))

    print("\n5. MARGIN OF SAFETY  (Graham: buy at a discount TO the estimate)")
    mid = sorted(per_share(x[2]) for x in anchors)[1]
    print("   midpoint of the three anchors: $%.2f/share" % mid)
    for mos in (0, 25, 33):
        print("     buy below %2d%% margin of safety : $%6.2f   %s"
              % (mos, mid * (1 - mos / 100),
                 "MARKET IS ABOVE THIS" if a.price > mid * (1 - mos / 100) else "market qualifies"))
    print("   market price $%.2f  vs  midpoint estimate $%.2f  ->  %+.0f%%"
          % (a.price, mid, 100 * (a.price / mid - 1)))

    # ---- the honest question: not "is my model right" but "what does the
    # price require you to believe?"  Reverse the DCF and solve for growth.
    print("\n6. REVERSE DCF -- what the market price REQUIRES")
    print("   (arguing about my growth assumption is a waste of breath; this")
    print("    asks what growth the price itself is quoting.)")
    for hurdle in (8, 9, 10, 12):
        rr = hurdle / 100.0
        lo, hi = -0.20, 0.60
        for _ in range(200):
            midg = (lo + hi) / 2
            if dcf(norm_oe, midg, rr, min(tg, rr - 0.005)) < mcap:
                lo = midg
            else:
                hi = midg
        print("     at a %2d%% required return, price $%.2f implies %5.1f%% owner-earnings"
              " growth for 10 yrs" % (hurdle, a.price, 100 * midg))

    print("\n7. SENSITIVITY -- value per share (rows: growth, cols: required return)")
    hurdles = [7, 8, 9, 10, 12]
    print("        %s" % "".join("%9d%%" % h for h in hurdles))
    for gg in (2, 4, 6, 8, 10):
        cells = []
        for h in hurdles:
            rr = h / 100.0
            cells.append("%10.2f" % per_share(dcf(norm_oe, gg / 100.0, rr,
                                                  min(tg, rr - 0.005))))
        flag = "".join(cells)
        print("   %2d%%  %s" % (gg, flag))
    print("   market price $%.2f -- cells ABOVE it are combinations that justify"
          " the price" % a.price)
    print("=" * 78)


if __name__ == "__main__":
    main()
