"""The six connector tools: definitions (what tools/list returns) and the calls.

Every number comes from a script that already exists in this repo, so the CLI
and the connector can't disagree:
  as_reported_financials   -> sec_financials.build / ttm
  owner_earnings_valuation -> owner_earnings.valuation
  compare_valuations       -> compare.screen / verdict
  realized_pnl             -> load.normalize + realized_pnl.realized / summary
  stop_coverage_check      -> gate_check.tier1 / parse_journal_levels / tier2
  rebalance_order_list     -> rebalance.plan_frame

None of them can reach a broker. There is no order placement, preview or
staging anywhere in this package, and no model is called: the tools return
numbers and the person's own Claude does the reasoning.
"""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import compare as cmp
import gate_check as gc
import load
import owner_earnings as oe
import realized_pnl as rp
import rebalance as rb
import sec_financials as sf

from . import inputs, treasury

REPO = "https://github.com/2016judea/trading-harness"
NOT_ADVICE = ("Calculated from SEC filings and the data provided. Not investment advice. "
              "Nothing here can place or preview an order.")

SERVER_INSTRUCTIONS = (
    "Trading Harness calculates; it does not trade. It has no brokerage connection and "
    "cannot place, preview or stage an order. Two kinds of tool: valuation from a "
    "company's SEC filings (as_reported_financials, owner_earnings_valuation, "
    "compare_valuations), and arithmetic on the person's own broker exports pasted into "
    "the chat (realized_pnl, stop_coverage_check, rebalance_order_list). "
    "Quote numbers exactly as returned and never estimate one a tool did not return. "
    "A result with refused set is an answer: the business has no durable owner earnings "
    "to value (or the latest year is a loss, or owner earnings outrun real cash flow), "
    "so report the reason and do not compute a value yourself. "
    "The tools have no price feed: pass the current share price from the person or a "
    "quote you can cite, and say what date it is from. The long-bond yield defaults to "
    "the latest 30-year Treasury par yield and its date is returned. "
    "Absolute values move with the hurdle rate; at a 10% hurdle against a 5%+ long bond "
    "this model reads most businesses as overvalued, Coca-Cola included, so check a "
    "single verdict against a business Berkshire Hathaway owns at the same inputs. "
    "The reverse DCF (growth the price requires) and the compare gap are the readings "
    "that do not depend on the hurdle. "
    "For pasted CSVs: E*TRADE's download, this repo's CSVs and plain columns (date, "
    "action, symbol, quantity, price, fee) are all read; each result says which columns "
    "it used and which rows it set aside. In stop_coverage_check an UNCHECKED row is not a "
    "passing row. An order list from rebalance_order_list is arithmetic, not an order: "
    "the person places any trade themselves at their broker. "
    "This is not investment advice; say so when the person is deciding a trade. "
    f"Source code and issues: {REPO}"
)

RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}


def _ann(title, open_world):
    return {"title": title, **RO, "openWorldHint": open_world}


TICKER = {"type": "string", "minLength": 1, "maxLength": 12,
          "description": "US-listed ticker that files with the SEC, e.g. KO or BRK.B"}
BOND = {"type": "number", "minimum": 0.1, "maximum": 20,
        "description": "Long-bond yield in percent (5.4 = 5.4%). Default: the latest "
                       "30-year Treasury par yield, fetched from treasury.gov"}
PRICE = {"type": "number", "exclusiveMinimum": 0, "description": "Share price in dollars"}

TOOLS = [
    {
        "name": "as_reported_financials",
        "title": "As-reported financials",
        "description": (
            "A company's annual figures exactly as filed with the SEC (XBRL companyfacts, "
            "10-K/20-F/40-F): revenue, gross profit, operating income, net income, cash "
            "from operations, capex, D&A, stock comp, dividends, buybacks, assets, "
            "liabilities, equity, cash, goodwill, intangibles, debt and diluted shares, by "
            "fiscal year, with the XBRL tag behind each line and the filing each year came "
            "from. Optionally a trailing-twelve-month column built from 10-Q year-to-date "
            "facts. Keyed on the period a fact covers, not the filing it appeared in. "
            'Examples: {"ticker": "KO"}; {"ticker": "CTVA", "years": 5, "ttm": true}.'),
        "inputSchema": {"type": "object", "properties": {
            "ticker": TICKER,
            "years": {"type": "integer", "minimum": 1, "maximum": 15,
                      "description": "Fiscal years, most recent last (default 10)"},
            "ttm": {"type": "boolean", "description": "Add trailing twelve months (default false)"},
        }, "required": ["ticker"], "additionalProperties": False},
        "annotations": _ann("As-reported financials", True),
    },
    {
        "name": "owner_earnings_valuation",
        "title": "Owner earnings and reverse DCF",
        "description": (
            "Owner earnings by year (net income + depreciation + amortisation - capex, "
            "Buffett 1986) beside free cash flow as a cross-check, return on equity and on "
            "tangible capital, and, given a share price, the owner-earnings yield against "
            "the long bond, three value anchors per share (bond-equivalent, two-stage DCF on "
            "normalised and on latest owner earnings), a margin-of-safety ladder, a reverse "
            "DCF (the owner-earnings growth the price requires at 8, 9, 10 and 12% required "
            "returns) and a growth x required-return sensitivity grid. Returns refused with "
            "a reason, and no value, when fewer than three of the last five years had "
            "positive owner earnings, the latest year was a loss, or owner earnings exceed "
            "1.6x actual free cash flow. Inputs from SEC filings; price supplied by the "
            "caller. "
            'Examples: {"ticker": "KO", "price": 70.1}; {"ticker": "CTVA", "price": 62.5, '
            '"long_bond_pct": 5.2, "hurdle_pct": 9}; {"ticker": "DE"} (history only).'),
        "inputSchema": {"type": "object", "properties": {
            "ticker": TICKER,
            "price": PRICE,
            "long_bond_pct": BOND,
            "growth_pct": {"type": "number", "minimum": -10, "maximum": 30,
                           "description": "Stage-1 owner-earnings growth for 10 years, % (default 4)"},
            "hurdle_pct": {"type": "number", "minimum": 3, "maximum": 30,
                           "description": "Required return, % (default 10)"},
            "terminal_pct": {"type": "number", "minimum": 0, "maximum": 5,
                             "description": "Terminal growth, % (default 2.5)"},
            "shares_millions": {"type": "number", "exclusiveMinimum": 0,
                                "description": "Shares outstanding in millions (default: "
                                               "the latest count on the company's filings)"},
            "years": {"type": "integer", "minimum": 5, "maximum": 12,
                      "description": "Fiscal years of history (default 7)"},
        }, "required": ["ticker"], "additionalProperties": False},
        "annotations": _ann("Owner earnings and reverse DCF", True),
    },
    {
        "name": "compare_valuations",
        "title": "Compare valuations",
        "description": (
            "Ranks 2 to 8 companies on the expectations gap: the owner-earnings growth each "
            "price requires (reverse DCF) minus the growth the business actually delivered "
            "over its last five fiscal years. Negative means the price asks for less than "
            "the company has already done. Per company: owner-earnings yield, return on "
            "tangible capital, ROE, gross margin first and last year, revenue and "
            "owner-earnings CAGR, the base used (latest year for a steady riser, an average "
            "for a cyclical) and a reading; refusals and do-not-extrapolate cases are "
            "labelled instead of ranked. The gap does not move with the hurdle the way "
            "absolute values do. "
            'Example: {"companies": [{"ticker": "KO", "price": 70.1}, {"ticker": "PEP", '
            '"price": 150.2}, {"ticker": "KDP", "price": 33.4}]}.'),
        "inputSchema": {"type": "object", "properties": {
            "companies": {"type": "array", "minItems": 2, "maxItems": 8,
                          "items": {"type": "object", "properties": {
                              "ticker": TICKER, "price": PRICE},
                              "required": ["ticker", "price"], "additionalProperties": False},
                          "description": "Ticker and current share price for each company"},
            "long_bond_pct": BOND,
            "hurdle_pct": {"type": "number", "minimum": 3, "maximum": 30,
                           "description": "Required return for the reverse DCF, % (default 8)"},
        }, "required": ["companies"], "additionalProperties": False},
        "annotations": _ann("Compare valuations", True),
    },
    {
        "name": "realized_pnl",
        "title": "Realized P&L (FIFO)",
        "description": (
            "Realized profit and loss from a pasted transactions export: sells matched "
            "first-in-first-out against earlier buys per symbol, fees on both sides taken "
            "out of the gain. Returns win rate, net, average win and loss, payoff ratio, "
            "average holding period in calendar days, totals by symbol, the worst and best "
            "closed lots, and sells with no buy in the data (reported apart, not counted "
            "as zero-cost wins). Reads E*TRADE's transaction download, the CSV this repo's "
            "fetcher writes, or any CSV with columns date, action (buy/sell), symbol, "
            "quantity, price and optionally fee. Options rows are set aside and counted. "
            "Nothing pasted is stored. "
            'Example: {"transactions_csv": "date,action,symbol,quantity,price,fee\\n'
            '2025-01-06,buy,KO,100,61.20,0\\n2025-09-02,sell,KO,100,69.85,0"}.'),
        "inputSchema": {"type": "object", "properties": {
            "transactions_csv": {"type": "string", "minLength": 10,
                                 "description": "The transactions export as CSV text, header row included"},
            "symbol": {"type": "string", "description": "Limit the result to one symbol"},
        }, "required": ["transactions_csv"], "additionalProperties": False},
        "annotations": _ann("Realized P&L (FIFO)", False),
    },
    {
        "name": "stop_coverage_check",
        "title": "Stop coverage check",
        "description": (
            "Compares positions with the orders actually resting at the broker. Flags: a "
            "position over the size cap with no resting stop, or a stop covering only part "
            "of it; any good-for-day order (gone at the close); resting sells exceeding "
            "shares held with no link between the legs; resting orders in names not held. "
            "With journal text in this repo's JOURNAL.template.md format, it also checks "
            "each written price gate (stops, sell limits, dated exits) against the resting "
            "orders and lists any gate it could not read as UNCHECKED rather than passing. "
            "Positions: symbol, quantity, and market value or last price. Orders: symbol, "
            "side, quantity, order type, limit price, stop price, term (GTC/day), status; "
            'the text "none" means no open orders. Nothing pasted is stored. '
            'Example: {"positions_csv": "symbol,quantity,last_price\\nKO,200,70.10\\n'
            'AAPL,120,229.80", "orders_csv": "symbol,side,quantity,order_type,stop_price,'
            'term\\nKO,sell,200,stop,62,GTC", "cap_pct": 25}.'),
        "inputSchema": {"type": "object", "properties": {
            "positions_csv": {"type": "string", "minLength": 5,
                              "description": "Positions export as CSV text, header row included"},
            "orders_csv": {"type": "string", "minLength": 1,
                           "description": 'Open orders as CSV text, or "none"'},
            "journal_text": {"type": "string",
                             "description": "Optional trading journal in this repo's template format"},
            "cap_pct": {"type": "number", "minimum": 1, "maximum": 100,
                        "description": "Position size, % of the account, above which a stop "
                                       "is expected (default 28)"},
            "cash": {"type": "number", "minimum": 0,
                     "description": "Cash in the account, for position weights (default: a "
                                    "cash row in the export, else 0)"},
        }, "required": ["positions_csv", "orders_csv"], "additionalProperties": False},
        "annotations": _ann("Stop coverage check", False),
    },
    {
        "name": "rebalance_order_list",
        "title": "Rebalance order list",
        "description": (
            "The dollar and share change per name to move a pasted positions export to "
            "target weights, as a list. Weights are a share of investable capital "
            "(positions + cash on hand - cash reserve). Each row: current $ and %, target "
            "%, dollar change, shares at the last price, direction, and whether exiting it "
            "would realize a gain or harvest a loss (from unrealized P&L; holding periods "
            "are not in a positions export, so short- vs long-term is not known). A "
            "target with no holding and no price is sized in dollars only. Arithmetic "
            "only: no order is placed or previewed. Nothing pasted is stored. "
            'Example: {"positions_csv": "symbol,quantity,last_price,total_gain\\n'
            'AAPL,120,229.80,5940\\nKO,200,70.10,-210", "target_weights_pct": {"AAPL": 30, '
            '"KO": 30, "VTI": 40}, "cash_on_hand": 10000, "prices": {"VTI": 301.55}}.'),
        "inputSchema": {"type": "object", "properties": {
            "positions_csv": {"type": "string", "minLength": 5,
                              "description": "Positions export as CSV text, header row included"},
            "target_weights_pct": {"type": "object",
                                   "additionalProperties": {"type": "number", "minimum": 0,
                                                            "maximum": 100},
                                   "description": "Symbol -> target weight in percent "
                                                  "(30 = 30%); 0 is a full exit"},
            "cash_on_hand": {"type": "number", "minimum": 0,
                             "description": "Uninvested cash (default: a cash row in the export, else 0)"},
            "cash_reserve": {"type": "number", "minimum": 0,
                             "description": "Cash to keep out of the allocation (default 0)"},
            "prices": {"type": "object", "additionalProperties": {"type": "number",
                                                                    "exclusiveMinimum": 0},
                       "description": "Symbol -> price, for targets not currently held"},
            "sell_unlisted": {"type": "boolean",
                              "description": "Also exit held names absent from the targets (default false)"},
        }, "required": ["positions_csv", "target_weights_pct"], "additionalProperties": False},
        "annotations": _ann("Rebalance order list", False),
    },
]

TOOL_NAMES = [t["name"] for t in TOOLS]


class ToolError(ValueError):
    """An answer, not a crash: the message goes back to the model as isError."""


# ------------------------------------------------------------------ helpers

def _f(v):
    """JSON-safe float (NaN -> None)."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _r(v, n=2):
    v = _f(v)
    return None if v is None else round(v, n)


def _m(v):
    """$ in millions, for text."""
    v = _f(v)
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1e9:
        return "%s$%.2fB" % ("-" if v < 0 else "", a / 1e9)
    return "%s$%.0fM" % ("-" if v < 0 else "", a / 1e6)


def _usd(v, cents=False):
    v = _f(v)
    if v is None:
        return "n/a"
    return ("-" if v < 0 else "") + ("$%s" % format(abs(v), ",.2f" if cents else ",.0f"))


def _pct(v, n=1):
    v = _f(v)
    return "n/a" if v is None else "%.*f%%" % (n, 100 * v)


def _bond(args):
    if args.get("long_bond_pct") is not None:
        return float(args["long_bond_pct"]), {"pct": float(args["long_bond_pct"]),
                                              "source": "supplied by caller"}
    b = treasury.long_bond("30 Yr")
    return b["pct"], b


NO_SEC = ("This server's SEC contact (SEC_USER_AGENT) is not configured, so SEC filings can't "
          "be read right now. The bring-your-own-data tools still work. Report it at "
          f"{REPO}/issues")


def _sec(fn):
    try:
        return fn()
    except sf.TickerNotFound as e:
        raise ToolError(str(e) + ". Only companies that file with the SEC are covered "
                        "(US-listed, including foreign filers of 20-F/40-F).")
    except sf.MissingContact:
        raise ToolError(NO_SEC)
    except treasury.BondUnavailable as e:
        raise ToolError(str(e))
    except KeyError as e:
        raise ToolError(f"The company's filings don't carry a line this needs ({e}). "
                        "Newer, foreign or unusual filers sometimes lack it.")


# ------------------------------------------------------------------ valuation

def as_reported_financials(args):
    ticker = args["ticker"]
    years = int(args.get("years") or 10)

    def run():
        b = sf.build(ticker, years)
        fx = sf.get(sf.FACTS.format(cik=b["cik"]))["facts"]
        src = sf.annual(fx, sf.LINES["net_income"], True) or sf.annual(fx, sf.LINES["revenue"], True)
        filings = {str(fy): {"form": v[1], "accession": v[2], "period_end": v[3]}
                   for fy, v in src.items() if fy in b["fiscal_years"]}
        ttm = {}
        if args.get("ttm"):
            for k in ("revenue", "net_income", "cfo", "capex", "d_and_a", "cogs"):
                v, why = sf.ttm(fx, sf.LINES[k])
                ttm[k] = {"value": _f(v), "basis": " ".join(str(why).split())}
        return b, filings, ttm

    b, filings, ttm = _sec(run)
    fys = b["fiscal_years"]
    data = {k: {str(fy): _f(v) for fy, v in row.items()} for k, row in b["data"].items() if row}
    lines = [f"**{b['name']} ({b['ticker']})**, CIK {b['cik']}. As reported, annual filings, "
             f"fiscal years {fys[0]}-{fys[-1]} ($M unless noted).", "",
             "| line | " + " | ".join(str(f) for f in fys) + " |",
             "|---|" + "---|" * len(fys)]
    for k, row in b["data"].items():
        if not row:
            continue
        cells = []
        for f in fys:
            v = row.get(f)
            cells.append("" if v is None else ("%.1f" % (v / 1e6) if k != "shares_diluted"
                                               else "%.1fM sh" % (v / 1e6)))
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    if ttm:
        lines += ["", "Trailing twelve months:"] + [
            f"- {k}: {_m(v['value']) if v['value'] is not None else 'n/a'} ({v['basis']})"
            for k, v in ttm.items()]
    edgar = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={b['cik']}&type=10-K"
    lines += ["", f"Filings: {edgar}", NOT_ADVICE]
    return ("\n".join(lines),
            {"ticker": b["ticker"], "name": b["name"], "cik": b["cik"], "fiscal_years": fys,
             "values": data, "xbrl_tags": b["tags_used"], "filings": filings,
             "ttm": ttm or None, "sec_filings_url": edgar,
             "units": "USD except shares_diluted (shares)"},
            {"n_tickers": 1})


def owner_earnings_valuation(args):
    ticker = args["ticker"]
    price = _f(args.get("price"))
    growth = float(args.get("growth_pct", 4.0))
    hurdle = float(args.get("hurdle_pct", 10.0))
    terminal = float(args.get("terminal_pct", 2.5))
    if terminal >= hurdle - 0.5:
        raise ToolError("terminal_pct must sit at least 0.5 points below hurdle_pct.")

    def run():
        bond, bond_meta = _bond(args) if price is not None else (None, None)
        v = oe.valuation(ticker, price, bond, args.get("shares_millions"), growth, hurdle,
                         terminal, int(args.get("years") or 7))
        return v, bond_meta

    v, bond_meta = _sec(run)
    fys = v["fiscal_years"]
    hist = [{"fiscal_year": f,
             "net_income": _f(v["rows"]["net_income"].get(f)),
             "depreciation": _f(v["rows"]["depreciation"].get(f)),
             "amortization": _f(v["rows"]["amortization"].get(f)),
             "capex": _f(v["rows"]["capex"].get(f)),
             "owner_earnings": _f(v["owner_earnings"].get(f)),
             "free_cash_flow": _f(v["fcf"].get(f))} for f in fys]
    head = f"**{v['name']} ({v['ticker']})**, CIK {v['cik']}. Owner earnings, $M:"
    tbl = ["| FY | net income | + D&A | - capex | = owner earnings | FCF check |",
           "|---|---|---|---|---|---|"]
    for h in hist:
        da = None
        if h["depreciation"] is not None and h["amortization"] is not None:
            da = h["depreciation"] + h["amortization"]
        elif v["rows"]["d_and_a"].get(h["fiscal_year"]) is not None:
            da = v["rows"]["d_and_a"][h["fiscal_year"]]
        tbl.append("| %s | %s | %s | %s | %s | %s |" % tuple(
            [h["fiscal_year"]] + ["" if x is None else "%.0f" % (x / 1e6) for x in
                                  (h["net_income"], da, h["capex"], h["owner_earnings"],
                                   h["free_cash_flow"])]))
    out = {"ticker": v["ticker"], "name": v["name"], "cik": v["cik"],
           "owner_earnings_by_year": hist, "refused": bool(v["refusal"]),
           "refusal_reason": v["refusal"], "oe_over_fcf": _r(v["oe_over_fcf"]),
           "capex_over_da": _r(v["capex_over_da"])}
    flags = []
    if v["capex_over_da"] and v["capex_over_da"] > 1.15:
        flags.append("capex is %.2fx D&A: part of it is growth capex, so owner earnings "
                     "(which subtract all capex) are understated" % v["capex_over_da"])
    if len(fys) < 5:
        flags.append("only %d fiscal years on file (spin-off or recent listing): "
                     "averages and growth rates are fragile" % len(fys))
    out["flags"] = flags
    if v["refusal"]:
        text = "\n".join([head, "", *tbl, "",
                          f"**Refused: {v['refusal']}.** No value is computed, because a value "
                          "beside a refusal reads as a value.", *[f"- {x}" for x in flags], "",
                          NOT_ADVICE])
        return text, out, {"n_tickers": 1, "refused": True}

    c = v["capital"]
    out.update(normalised_owner_earnings=_f(v["norm_oe"]), normalised_years=v["n_norm_oe"],
               normalised_fcf=_f(v["norm_fcf"]), latest_fiscal_year=v["latest"],
               capital={k: (_r(x, 4) if k in ("roe", "oe_on_equity", "rotc") else _f(x))
                        for k, x in c.items()})
    lines = [head, "", *tbl, "",
             f"Normalised owner earnings ({v['n_norm_oe']}-yr avg): {_m(v['norm_oe'])}; "
             f"normalised FCF: {_m(v['norm_fcf'])}.",
             f"ROE {_pct(c['roe'])}; owner earnings on equity {_pct(c['oe_on_equity'])}; "
             + (f"on tangible capital {_pct(c['rotc'])}." if c["rotc"] is not None else
                f"tangible capital is {_m(c['tangible'])} (negative or nil), so return on "
                "tangible capital is not meaningful.")]
    if price is None:
        lines += ["", "No price given, so the market section (yield, value anchors, margin of "
                      "safety, reverse DCF) is not computed.", *[f"- {x}" for x in flags], "",
                  NOT_ADVICE]
        return "\n".join(lines), out, {"n_tickers": 1}

    mk = v["market"]
    out.update(
        inputs={"price": price, "long_bond_pct": v["bond"], "long_bond": bond_meta,
                "growth_pct": growth, "hurdle_pct": hurdle, "terminal_pct": terminal},
        market={"shares": _f(mk["shares"]), "market_cap": _f(mk["mcap"]), "debt": _f(mk["debt"]),
                "cash": _f(mk["cash"]), "enterprise_value": _f(mk["ev"]),
                "owner_earnings_yield": {k: _r(y, 4) for k, y in mk["yields"]}},
        anchors=[{"anchor": a["label"].split(":")[0].strip(), "description": a["label"] + " " + a["basis"],
                  "value": _f(a["value"]), "per_share": _r(a["per_share"]),
                  "price_vs_value_pct": _r(a["price_vs_value_pct"], 1)} for a in v["anchors"]],
        midpoint_per_share=_r(v["midpoint_per_share"]),
        margin_of_safety=[{"margin_pct": r["mos_pct"], "price_at_or_below": _r(r["buy_below"]),
                           "market_price_is_above": r["market_above"]} for r in v["margin_of_safety"]],
        reverse_dcf=[{"required_return_pct": r["required_return_pct"],
                      "implied_owner_earnings_growth": _r(r["implied_growth"], 4)}
                     for r in v["reverse_dcf"]],
        sensitivity={"required_return_pct": v["sensitivity"]["hurdles_pct"],
                     "rows": [{"growth_pct": r["growth_pct"],
                               "value_per_share": [_r(x) for x in r["per_share"]]}
                              for r in v["sensitivity"]["rows"]]},
        calibration_note=("At a 10% hurdle against a 5%+ long bond this model reads most "
                          "businesses as overvalued, Coca-Cola included. The reverse DCF does "
                          "not depend on the hurdle; the anchors do."))
    bsrc = (f"{v['bond']:.2f}% ({bond_meta.get('tenor', '')} Treasury, {bond_meta['date']})"
            if bond_meta and bond_meta.get("date") else f"{v['bond']:.2f}% (supplied)")
    lines += ["",
              f"At ${price:,.2f}: market cap {_m(mk['mcap'])}, EV {_m(mk['ev'])}. Owner-earnings "
              f"yield {_pct(mk['yields'][0][1], 2)} on normalised (latest year "
              f"{_pct(mk['yields'][2][1], 2)}) vs long bond {bsrc}.",
              "", "Value per share:"]
    lines += [f"- {a['label']} {a['basis']}: ${a['per_share']:,.2f} (price {a['price_vs_value_pct']:+.0f}%)"
              for a in v["anchors"]]
    lines += [f"- midpoint ${v['midpoint_per_share']:,.2f}; at a 25% margin of safety "
              f"${v['margin_of_safety'][1]['buy_below']:,.2f}, at 33% "
              f"${v['margin_of_safety'][2]['buy_below']:,.2f}",
              "", "Reverse DCF, owner-earnings growth for 10 years that the price requires: " +
              ", ".join(f"{r['implied_growth'] * 100:.1f}% at a {r['required_return_pct']}% return"
                        for r in v["reverse_dcf"]) + ".",
              *[f"- {x}" for x in flags], "",
              "Calibration: " + out["calibration_note"], NOT_ADVICE]
    return "\n".join(lines), out, {"n_tickers": 1}


def compare_valuations(args):
    comps = args["companies"]
    hurdle = float(args.get("hurdle_pct", 8.0))
    seen = set()
    pairs = []
    for c in comps:
        t = str(c["ticker"]).upper().strip()
        if t in seen:
            continue
        seen.add(t)
        pairs.append((t, float(c["price"])))

    def run():
        bond, meta = _bond(args)
        rows, bad = cmp.screen(pairs, bond, hurdle)
        return bond, meta, rows, bad

    bond, meta, rows, bad = _sec(run)
    if bad and all("SEC_USER_AGENT" in r.get("error", "") for r in bad) and not rows:
        raise ToolError(NO_SEC)
    ranked = cmp.by_gap(rows)
    out_rows = [{
        "ticker": r["ticker"], "name": r["name"], "price": r["price"],
        "market_cap": _f(r["mcap"]), "owner_earnings_yield": _r(r["oe_yield"], 4),
        "return_on_tangible_capital": _r(r["rotc"], 4), "roe": _r(r["roe"], 4),
        "gross_margin_first": _r(r["gm_first"], 4), "gross_margin_last": _r(r["gm_last"], 4),
        "revenue_cagr": _r(r["rev_cagr"], 4), "owner_earnings_cagr": _r(r["oe_cagr"], 4),
        "implied_growth": _r(r["implied"], 4), "gap": _r(r["gap"], 4),
        "reading": r["verdict"], "reading_label": r["verdict_label"],
        "refused": r["verdict"] == "refused",
        "basis": r["basis"], "window": f"FY{r['cagr_from']}-FY{r['fy_last']}",
        "fiscal_years_on_file": r["n_years"], "capex_over_da": _r(r["capex_over_da"]),
    } for r in ranked]
    bsrc = (f"{bond:.2f}% (30-yr Treasury, {meta['date']})" if meta.get("date")
            else f"{bond:.2f}% (supplied)")
    lines = [f"Expectations gap = growth the price requires ({hurdle:.0f}% required return) minus "
             f"owner-earnings growth delivered. Long bond {bsrc}.", "",
             "| | price | implied g | delivered g | gap | reading |", "|---|---|---|---|---|---|"]
    for r in out_rows:
        lines.append("| %s | $%.2f | %s | %s | %s | %s |" % (
            r["ticker"], r["price"], _pct(r["implied_growth"]), _pct(r["owner_earnings_cagr"]),
            "n/a" if r["gap"] is None else "%+.1fpt" % (100 * r["gap"]), r["reading_label"]))
    for r in out_rows:
        if r["fiscal_years_on_file"] < 5:
            lines.append(f"- {r['ticker']}: only {r['fiscal_years_on_file']} fiscal years on file; "
                         "its growth rate is fragile.")
    for r in bad:
        lines.append(f"- {r['ticker']}: excluded, {r['error']}")
    lines += ["", "Negative gap: the price asks for less growth than the business has "
                  "delivered. Base used per name is in basis.", NOT_ADVICE]
    return ("\n".join(lines),
            {"long_bond_pct": bond, "long_bond": meta, "hurdle_pct": hurdle,
             "ranked": out_rows, "excluded": [{"ticker": r["ticker"], "reason": r["error"]}
                                              for r in bad]},
            {"n_tickers": len(pairs), "refused": sum(r["refused"] for r in out_rows)})


# ------------------------------------------------------------------ your own data

def realized_pnl(args):
    raw, rep = inputs.transactions(args["transactions_csv"])
    df = load.normalize(raw)
    sym = (args.get("symbol") or "").strip().upper()
    if sym:
        df = df[df["brokerage.displaySymbol"].astype(str).str.upper().eq(sym) | ~df["is_trade"]]
    closed, orphans = rp.realized(df)
    st = rp.summary(closed, orphans) if len(closed) else {}
    window = df[df["is_trade"]]["transactionDate"]
    span = (f"{window.min():%Y-%m-%d} to {window.max():%Y-%m-%d}" if len(window) else "no trades")

    def lot(r):
        return {"symbol": r["symbol"], "qty": _r(r["qty"], 4),
                "buy_date": f"{r['buy_date']:%Y-%m-%d}", "buy_price": _r(r["buy_price"], 4),
                "sell_date": f"{r['sell_date']:%Y-%m-%d}", "sell_price": _r(r["sell_price"], 4),
                "gain": _r(r["gain"]), "return_pct": _r(r["return_pct"]),
                "hold_days": int(r["hold_days"])}

    by_sym, worst, best = [], [], []
    if len(closed):
        g = (closed.groupby("symbol").agg(lots=("gain", "size"), realized=("gain", "sum"),
                                          avg_return_pct=("return_pct", "mean"),
                                          avg_hold_days=("hold_days", "mean"))
             .sort_values("realized"))
        by_sym = [{"symbol": s, "lots": int(r["lots"]), "realized": _r(r["realized"]),
                   "avg_return_pct": _r(r["avg_return_pct"]),
                   "avg_hold_days": _r(r["avg_hold_days"], 0)} for s, r in g.iterrows()]
        srt = closed.sort_values("gain")
        worst = [lot(r) for _, r in srt.head(10).iterrows()]
        best = [lot(r) for _, r in srt.tail(10).iloc[::-1].iterrows()]
    orph = [{"symbol": r["symbol"], "qty": _r(r["qty"], 4), "sell_date": f"{r['sell_date']:%Y-%m-%d}",
             "sell_price": _r(r["sell_price"], 4), "proceeds": _r(r["proceeds"])}
            for _, r in (orphans.iterrows() if len(orphans) else [])]

    lines = [f"Read {rep['rows_read']} rows ({rep['format']}); {rep['trades_used']} buy/sell rows "
             f"used, {span}."]
    if st:
        lines += [f"**{st['lots']} closed lot{'s' if st['lots'] != 1 else ''}, net realized {_usd(st['net'])}.** Win rate "
                  f"{st['win_rate_pct']:.1f}% ({st['wins']} won, {st['losses']} lost); average win "
                  f"{_usd(st['avg_win'])}, average loss {_usd(st['avg_loss'])}"
                  + (f", payoff {st['payoff']:.2f}x" if st.get("payoff") else "")
                  + f"; average hold {st['avg_hold_days']:.0f} days."]
        lines += ["", "By symbol (worst first):"] + [
            f"- {b['symbol']}: {_usd(b['realized'])} over {b['lots']} lot(s), avg "
            f"{b['avg_return_pct']:+.1f}%, {b['avg_hold_days']:.0f} days" for b in by_sym[:25]]
        if len(by_sym) > 25:
            lines.append(f"- … {len(by_sym) - 25} more in by_symbol")
    else:
        lines.append("No sell matched a buy in this data, so there is no closed lot to report.")
    if orph:
        lines += ["", f"{len(orph)} sell(s) with no buy in the data (proceeds "
                      f"{_usd(sum(o['proceeds'] or 0 for o in orph))}): their cost basis predates "
                      "the export, so their P&L is unknown and they are left out of the totals, "
                      "not counted as zero-cost wins."]
    if rep.get("options_rows_set_aside"):
        lines.append(f"- {rep['options_rows_set_aside']} options row(s) set aside: contract "
                     "multipliers aren't applied here.")
    if rep.get("trade_rows_unreadable"):
        lines.append(f"- {rep['trade_rows_unreadable']} buy/sell row(s) lacked a readable date, "
                     "quantity or price and were skipped.")
    if rep.get("price_from_amount"):
        lines.append("- No price column: price backed out of the cash amount, fees included.")
    lines += ["", "Covers only the dates in the export; it is not lifetime performance. "
                  "Nothing pasted is stored.", NOT_ADVICE]
    return ("\n".join(lines),
            {"summary": {k: _r(v) if isinstance(v, float) else v for k, v in st.items()},
             "by_symbol": by_sym, "worst_lots": worst, "best_lots": best,
             "sells_without_basis": orph, "dates": span, "read": rep},
            {"n_rows": rep["rows_read"], "n_lots": int(st.get("lots", 0))})


SEV = {"CRIT": "critical", "WARN": "warning", "OK": "ok", "HOLE": "unchecked", "DORM": "dormant"}
ORDER_COLS = ["orderId", "symbol", "orderAction", "priceType", "orderedQuantity",
              "filledQuantity", "limitPrice", "stopPrice", "orderTerm", "status",
              "replacesOrderId", "replacedByOrderId"]


def stop_coverage_check(args):
    pf, prep = inputs.positions(args["positions_csv"])
    cash = args.get("cash")
    if cash is None:
        cash = prep.get("cash_row") or 0.0
    pf = inputs.with_pct(pf, cash)
    otext = (args.get("orders_csv") or "").strip()
    if otext.lower() in ("none", "no orders", "no open orders", "0", "[]", "-"):
        od, orep = pd.DataFrame(columns=ORDER_COLS), {"orders": 0, "stated": "no open orders"}
    else:
        od, orep = inputs.orders(otext)
    cap = float(args.get("cap_pct", gc.DEFAULT_CAP_PCT))
    findings = [("tier1", s, m) for s, m in gc.tier1(pf, od, cap)]
    jt = args.get("journal_text") or ""
    if jt.strip():
        levels, holes = gc.parse_journal_levels(jt)
        held = {str(x).upper() for x in pf["symbol"].dropna()}
        now = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
        findings += [("tier2", s, m) for s, m in gc.tier2(levels, holes, od, now, held)]
        t2 = f"{len(levels)} journal gate(s) parsed, {len(holes)} not parsed"
    else:
        t2 = "no journal given: written gates were not checked"
    counts = {v: sum(1 for _, s, _ in findings if SEV[s] == v) for v in SEV.values()}
    lines = [f"{prep['positions']} position(s), {orep['orders']} order row(s), cap {cap:.0f}% of "
             f"the account (cash {_usd(cash)}). {t2}.",
             f"**{counts['critical']} critical, {counts['warning']} warning, "
             f"{counts['unchecked']} unchecked, {counts['ok']} ok, {counts['dormant']} dormant.**", ""]
    order = {"critical": 0, "unchecked": 1, "warning": 2, "ok": 3, "dormant": 4}
    for tier, s, m in sorted(findings, key=lambda f: order[SEV[f[1]]]):
        lines.append(f"- {SEV[s].upper()}: {m}")
    notes = [orep.get("status_assumed"), orep.get("term_missing")]
    lines += [f"- note: {n}" for n in notes if n]
    if counts["unchecked"]:
        lines.append("An UNCHECKED gate is not a passing gate.")
    lines += ["", "Only as current as the lists pasted: an order that expired or filled since "
                  "they were copied isn't seen. Nothing pasted is stored.", NOT_ADVICE]
    return ("\n".join(lines),
            {"counts": counts, "cap_pct": cap, "cash": cash,
             "findings": [{"tier": t, "severity": SEV[s], "message": m} for t, s, m in findings],
             "positions": [{"symbol": r["symbol"], "quantity": _f(r["quantity"]),
                            "market_value": _r(r["marketValue"]),
                            "pct_of_account": _r(r["pctOfPortfolio"])} for _, r in pf.iterrows()],
             "read": {"positions": prep, "orders": orep}},
            {"n_rows": int(len(pf) + len(od)), "critical": counts["critical"]})


def rebalance_order_list(args):
    pf, prep = inputs.positions(args["positions_csv"])
    tw = {str(k).upper().strip(): float(v) for k, v in (args.get("target_weights_pct") or {}).items()}
    if not tw:
        raise ToolError("target_weights_pct is empty.")
    total = sum(tw.values())
    if total > 100 + 1e-6:
        raise ToolError(f"target weights sum to {total:.1f}%, over 100% of investable capital.")
    cash_on_hand = args.get("cash_on_hand")
    if cash_on_hand is None:
        cash_on_hand = prep.get("cash_row") or 0.0
    cfg = {"targets": {k: v / 100 for k, v in tw.items()},
           "prices": {str(k).upper(): float(v) for k, v in (args.get("prices") or {}).items()},
           "cash_on_hand": float(cash_on_hand), "cash_reserve": float(args.get("cash_reserve") or 0),
           "sell_unlisted": bool(args.get("sell_unlisted"))}
    try:
        rb.check_targets(cfg)
        orders, s = rb.plan_frame(pf, cfg)
    except ValueError as e:
        raise ToolError(str(e))
    no_gain = prep.get("no_gain_column")
    rows = []
    for _, r in orders.iterrows():
        rows.append({"symbol": r["symbol"], "current_usd": _f(r["cur_$"]), "current_pct": _f(r["cur_%"]),
                     "target_pct": _f(r["tgt_%"]), "change_usd": _f(r["delta_$"]),
                     "price": _r(r["price"], 4),
                     "shares": None if r["shares"] is None or pd.isna(r["shares"]) else int(r["shares"]),
                     "direction": {"BUY": "buy", "SELL": "sell"}.get(r["action"], "hold"),
                     "tax_on_exit": "unknown (no gain column)" if no_gain and r["tax_on_exit"] != "n/a"
                     else r["tax_on_exit"]})
    lines = [f"Capital {_usd(s['total'])} (positions + cash on hand {_usd(cash_on_hand)}), reserve "
             f"{_usd(s['cash_reserve'])}, investable {_usd(s['investable'])}.", "",
             "| symbol | now | target | change | shares | exit would |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s (%.1f%%) | %.1f%% | %s | %s | %s |" % (
            r["symbol"], _usd(r["current_usd"]), r["current_pct"], r["target_pct"],
            ("+" if (r["change_usd"] or 0) > 0 else "") + _usd(r["change_usd"]),
            "" if r["shares"] is None else ("%+d" % r["shares"]), r["tax_on_exit"]))
    lines += ["", f"Sales {_usd(s['proceeds'])}, purchases {_usd(s['net_buys'])}."]
    if s["unpriced"]:
        lines.append(f"No price for {', '.join(s['unpriced'])} (not held): sized in dollars only; "
                     "pass prices to size in shares.")
    lines += ["Holding periods aren't in a positions export, so a gain's tax rate (short- or "
              "long-term) isn't known here.",
              "This is an order list, not an order. Nothing was placed or previewed. Nothing "
              "pasted is stored.", NOT_ADVICE]
    return ("\n".join(lines),
            {"rows": rows, "total": _r(s["total"]), "investable": _r(s["investable"]),
             "cash_on_hand": _r(cash_on_hand), "cash_reserve": _r(s["cash_reserve"]),
             "sales": _r(s["proceeds"]), "purchases": _r(s["net_buys"]),
             "unpriced": s["unpriced"], "read": prep},
            {"n_rows": int(len(pf)), "n_tickers": len(tw)})


IMPL = {
    "as_reported_financials": as_reported_financials,
    "owner_earnings_valuation": owner_earnings_valuation,
    "compare_valuations": compare_valuations,
    "realized_pnl": realized_pnl,
    "stop_coverage_check": stop_coverage_check,
    "rebalance_order_list": rebalance_order_list,
}


def check_args(name: str, args: dict) -> None:
    """Required keys, types and bounds from the tool's own inputSchema, so a bad
    call gets a sentence back instead of a traceback."""
    schema = next(t["inputSchema"] for t in TOOLS if t["name"] == name)
    props = schema["properties"]
    for k in schema.get("required", []):
        if args.get(k) in (None, ""):
            raise ToolError(f"{name}: '{k}' is required.")
    for k, v in args.items():
        if k not in props:
            raise ToolError(f"{name}: unknown argument '{k}'. Accepted: {', '.join(props)}.")
        p = props[k]
        t = p.get("type")
        if v is None:
            continue
        if t == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
            raise ToolError(f"{name}: '{k}' must be a number.")
        if t == "integer" and (isinstance(v, bool) or not isinstance(v, int)):
            if isinstance(v, float) and v.is_integer():
                args[k] = int(v)
            else:
                raise ToolError(f"{name}: '{k}' must be a whole number.")
        if t == "string" and not isinstance(v, str):
            raise ToolError(f"{name}: '{k}' must be text.")
        if t == "boolean" and not isinstance(v, bool):
            raise ToolError(f"{name}: '{k}' must be true or false.")
        if t == "array" and not isinstance(v, list):
            raise ToolError(f"{name}: '{k}' must be a list.")
        if t == "object" and not isinstance(v, dict):
            raise ToolError(f"{name}: '{k}' must be an object.")
        if t in ("number", "integer"):
            if "minimum" in p and v < p["minimum"] or "maximum" in p and v > p["maximum"]:
                raise ToolError(f"{name}: '{k}' must be between {p.get('minimum')} and {p.get('maximum')}.")
            if "exclusiveMinimum" in p and v <= p["exclusiveMinimum"]:
                raise ToolError(f"{name}: '{k}' must be above {p['exclusiveMinimum']}.")
        if t == "array":
            if len(v) < p.get("minItems", 0) or len(v) > p.get("maxItems", 10**6):
                raise ToolError(f"{name}: '{k}' takes {p.get('minItems')} to {p.get('maxItems')} items.")
            for it in v:
                if not isinstance(it, dict) or not isinstance(it.get("ticker"), str) or \
                        isinstance(it.get("price"), bool) or not isinstance(it.get("price"), (int, float)) \
                        or it["price"] <= 0:
                    raise ToolError(f"{name}: each company needs a ticker and a price above 0.")


def call(name: str, args: dict):
    """-> (text, structured, info, is_error). Never raises for a bad input."""
    args = dict(args or {})
    try:
        check_args(name, args)
        text, structured, info = IMPL[name](args)
        return text, structured, info, False
    except (ToolError, inputs.InputError) as e:
        return str(e), None, {"err": "input"}, True
