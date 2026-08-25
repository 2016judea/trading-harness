#!/usr/bin/env python3
"""Pull EXACT as-reported financials from the SEC's free XBRL API.

No key, no account, no scraping. The only requirement is a real User-Agent
with a contact address (SEC blocks anonymous clients).

    python3 sec_financials.py CTVA            # 10 fiscal years, core lines
    python3 sec_financials.py CTVA --years 5
    python3 sec_financials.py CTVA --concept NetIncomeLoss   # raw one concept
    python3 sec_financials.py CTVA --json     # machine-readable

Why this exists: a valuation built on a number typed from memory is a
valuation of the memory. Every figure printed here carries the fiscal year,
the form it came from, and the accession number that reported it.
"""
import argparse, json, sys, os, time, hashlib, urllib.request
from collections import OrderedDict

# SEC requires a real contact address in the User-Agent and BLOCKS clients
# without one (blocks, not rate-limits). Set your own before first use:
#   export SEC_USER_AGENT="Your Name you@example.com"
UA = os.environ.get("SEC_USER_AGENT", "")
if not UA:
    sys.exit("Set SEC_USER_AGENT, e.g.\n"
             '  export SEC_USER_AGENT="Your Name you@example.com"\n'
             "SEC blocks requests that do not identify a contact.")
TICKERS = "https://www.sec.gov/files/company_tickers.json"
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# label -> ordered list of candidate us-gaap tags. First tag with data wins,
# because issuers use different tags for the same line and the alternates are
# not synonyms to be summed.
# IFRS aliases (foreign private issuers file 20-F/40-F with ifrs-full tags)
IFRS = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "cogs": ["CostOfSales"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["ProfitLossFromOperatingActivities"],
    "net_income": ["ProfitLoss"],
    "cfo": ["CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "d_and_a": ["DepreciationAndAmortisationExpense"],
    "assets": ["Assets"], "liabilities": ["Liabilities"],
    "equity": ["Equity"], "cash": ["CashAndCashEquivalents"],
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsOtherThanGoodwill"],
    "ppe_net": ["PropertyPlantAndEquipment"], "inventory": ["Inventories"],
}

LINES = OrderedDict([
    ("revenue",        ["RevenueFromContractWithCustomerExcludingAssessedTax",
                        "Revenues", "SalesRevenueNet",
                        "RevenueFromContractWithCustomerIncludingAssessedTax"]),
    ("cogs",           ["CostOfGoodsAndServicesSold", "CostOfRevenue"]),
    ("gross_profit",   ["GrossProfit"]),
    ("operating_income", ["OperatingIncomeLoss"]),
    ("net_income",     ["NetIncomeLoss", "ProfitLoss"]),
    ("cfo",            ["NetCashProvidedByUsedInOperatingActivities",
                        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    ("capex",          ["PaymentsToAcquireProductiveAssets",
                        "PaymentsToAcquirePropertyPlantAndEquipment"]),
    ("d_and_a",        ["DepreciationDepletionAndAmortization",
                        "DepreciationAmortizationAndAccretionNet",
                        "DepreciationAndAmortization"]),
    ("sbc",            ["ShareBasedCompensation"]),
    ("r_and_d",        ["ResearchAndDevelopmentExpense"]),
    ("dividends_paid", ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]),
    ("buybacks",       ["PaymentsForRepurchaseOfCommonStock"]),
    # --- balance sheet (point-in-time) ---
    ("assets",         ["Assets"]),
    ("liabilities",    ["Liabilities"]),
    ("equity",         ["StockholdersEquity",
                        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    ("cash",           ["CashAndCashEquivalentsAtCarryingValue",
                        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    ("goodwill",       ["Goodwill"]),
    ("intangibles",    ["IntangibleAssetsNetExcludingGoodwill",
                        "FiniteLivedIntangibleAssetsNet"]),
    ("ppe_net",        ["PropertyPlantAndEquipmentNet"]),
    ("inventory",      ["InventoryNet"]),
    ("lt_debt",        ["LongTermDebtNoncurrent", "LongTermDebt",
                        "LongTermDebtAndCapitalLeaseObligations"]),
    ("st_debt",        ["ShortTermBorrowings", "LongTermDebtCurrent",
                        "OtherShortTermBorrowings"]),
    ("shares_diluted", ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
])
for _k, _v in IFRS.items():
    if _k in LINES:
        LINES[_k] = LINES[_k] + [t for t in _v if t not in LINES[_k]]

FLOWS = {"revenue","gross_profit","operating_income","net_income","cfo","capex",
         "d_and_a","sbc","r_and_d","dividends_paid","buybacks","shares_diluted",
         "cogs"}


CACHE = os.path.join(os.path.expanduser("~"), ".cache", "sec-financials")
CACHE_TTL = 86400          # a day; 10-Ks do not change hourly


def get(url):
    """Cached fetch. companyfacts payloads run to several MB and a screen of 30
    names would otherwise pull ~200MB and get throttled."""
    os.makedirs(CACHE, exist_ok=True)
    key = os.path.join(CACHE, hashlib.md5(url.encode()).hexdigest() + ".json")
    if os.path.exists(key) and (time.time() - os.path.getmtime(key)) < CACHE_TTL:
        with open(key) as f:
            return json.load(f)
    data = _fetch(url)
    with open(key, "w") as f:
        json.dump(data, f)
    return data


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        import gzip; raw = gzip.decompress(raw)
    return json.loads(raw)


def resolve_cik(ticker):
    for row in get(TICKERS).values():
        if row["ticker"].upper() == ticker.upper():
            return int(row["cik_str"]), row["title"]
    sys.exit("ticker %s not found in SEC company_tickers.json" % ticker)


def annual(facts, tags, is_flow):
    """{fy: (value, form, accn, end, unit, tag)} keyed on the PERIOD, not the filing.

    ⚠️ THE TRAP: in companyfacts, `fy`/`fp` describe the FILING the fact appeared
    in, not the period the fact covers. A 10-K carries two prior years as
    comparatives, all stamped with the filing's fy. Corteva's FY2019 D&A
    (1,599M) is stamped fy=2019, fy=2020 AND fy=2021 -- keying on fy returns the
    same stale year three times and it looks like a flat trend. Key on `end`.

    Restatements: the same period appears in several filings. Latest `filed` wins.
    Tags: candidates are tried in priority order PER PERIOD, because issuers
    switch tags mid-history (CTVA moved capex from PaymentsToAcquirePropertyPlant
    AndEquipment to PaymentsToAcquireProductiveAssets in FY2022).
    """
    out, chosen_rank = {}, {}
    forms = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
    for rank, tag in enumerate(tags):
        node = facts.get("us-gaap", {}).get(tag) or facts.get("ifrs-full", {}).get(tag)
        if not node:
            continue
        for unit, items in node["units"].items():
            for it in items:
                if it.get("form") not in forms:
                    continue
                end = _d(it["end"])
                if is_flow:
                    if not it.get("start"):
                        continue
                    if not 300 <= (end - _d(it["start"])).days <= 400:
                        continue
                fy = end.year if end.month >= 6 else end.year - 1
                cand = (it["val"], it["form"], it.get("accn"), it["end"], unit, tag)
                filed = it.get("filed", "")
                prev = out.get(fy)
                if prev is None or rank < chosen_rank.get(fy, (99, ""))[0] or (
                        rank == chosen_rank[fy][0] and filed > chosen_rank[fy][1]):
                    out[fy], chosen_rank[fy] = cand, (rank, filed)
    return out


def ttm(facts, tags):
    """Trailing-twelve-month flow: last full FY + current YTD - prior-year YTD.

    Uses 10-Q year-to-date facts (start = fiscal-year start). Returns
    (value, label) or (None, reason) -- never guesses.
    """
    ytd = {}
    for rank, tag in enumerate(tags):
        node = facts.get("us-gaap", {}).get(tag)
        if not node:
            continue
        for unit, items in node["units"].items():
            for it in items:
                if it.get("form") not in ("10-Q", "10-Q/A") or not it.get("start"):
                    continue
                s, e = _d(it["start"]), _d(it["end"])
                days = (e - s).days
                if days < 80 or days > 320:
                    continue
                if s.month > 3:          # not a year-to-date stub
                    continue
                key = (e.year, round(days / 91.0))    # (year, quarters elapsed)
                if key not in ytd or it.get("filed", "") > ytd[key][1]:
                    ytd[key] = (it["val"], it.get("filed", ""), it["end"], days)
        if ytd:
            break
    if not ytd:
        return None, "no 10-Q year-to-date facts"
    (yr, q) = max(ytd)
    cur = ytd[(yr, q)]
    prior = ytd.get((yr - 1, q))
    if prior is None:
        return None, "no prior-year YTD at Q%d to subtract" % q
    fy = annual(facts, tags, True)
    base = fy.get(yr - 1)
    if base is None:
        return None, "no FY%d base" % (yr - 1)
    return (base[0] + cur[0] - prior[0],
            "FY%d %s + %s YTD %s - prior YTD %s" % (
                yr - 1, fmt(base[0]), cur[2], fmt(cur[0]), fmt(prior[0])))

def _d(s):
    import datetime
    return datetime.date(*map(int, s.split("-")))


def build(ticker, years):
    cik, name = resolve_cik(ticker)
    fx = get(FACTS.format(cik=cik))["facts"]
    series = {k: annual(fx, tags, k in FLOWS) for k, tags in LINES.items()}
    gp, rev, cg = series["gross_profit"], series["revenue"], series["cogs"]
    for fy in rev:
        if fy not in gp and fy in cg:
            gp[fy] = (rev[fy][0] - cg[fy][0], "derived", "revenue-cogs",
                      rev[fy][3], "USD", "DERIVED")
    fys = sorted({fy for s in series.values() for fy in s})[-years:]
    return {"ticker": ticker.upper(), "name": name, "cik": cik,
            "fiscal_years": fys,
            "data": {k: {fy: v[0] for fy, v in s.items() if fy in fys}
                     for k, s in series.items()},
            "tags_used": {k: (list(s.values())[0][5] if s else None)
                          for k, s in series.items()}}


def fmt(v):
    if v is None: return "        —"
    a = abs(v)
    if a >= 1e9:  return "%8.2fB" % (v/1e9)
    if a >= 1e6:  return "%8.1fM" % (v/1e6)
    return "%9.0f" % v


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ticker"); p.add_argument("--years", type=int, default=10)
    p.add_argument("--concept"); p.add_argument("--json", action="store_true")
    p.add_argument("--ttm", action="store_true", help="trailing-twelve-month flows")
    a = p.parse_args()

    if a.concept:
        cik, name = resolve_cik(a.ticker)
        fx = get(FACTS.format(cik=cik))["facts"]
        for ns in ("us-gaap", "dei", "ifrs-full"):
            if a.concept in fx.get(ns, {}):
                node = fx[ns][a.concept]
                print("%s  [%s]  %s" % (a.concept, ns, node.get("label")))
                for unit, items in node["units"].items():
                    for it in items:
                        print("  %-8s %-10s %-6s %-4s %14.2f  %s" % (
                            it.get("form"), it["end"], it.get("fy"), it.get("fp"),
                            it["val"], it.get("start", "")))
                return
        sys.exit("concept not found")

    b = build(a.ticker, a.years)
    if a.json:
        print(json.dumps(b, indent=2)); return
    print("%s — %s   CIK %d   (as reported, annual filings, XBRL)"
          % (b["ticker"], b["name"], b["cik"]))
    fys = b["fiscal_years"]
    print("\n%-18s %s" % ("FY", "".join("%10s" % f for f in fys)))
    print("-" * (18 + 10*len(fys)))
    if a.ttm:
        cik, _ = resolve_cik(a.ticker)
        fx = get(FACTS.format(cik=cik))["facts"]
        print("\nTRAILING TWELVE MONTHS (from 10-Q year-to-date facts)")
        for k in ("revenue", "net_income", "cfo", "capex", "d_and_a", "cogs"):
            v, why = ttm(fx, LINES[k])
            print("  %-16s %s   %s" % (k, fmt(v) if v is not None else "       n/a", why))
        print()
    for k in LINES:
        row = b["data"][k]
        if not row: continue
        print("%-18s %s   <%s>" % (k, "".join(" %s" % fmt(row.get(f)) for f in fys),
                                   b["tags_used"][k]))


if __name__ == "__main__":
    main()
