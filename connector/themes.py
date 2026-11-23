"""Theme doors: one named tool per kind of question people bring, each a thin wrapper
over the six engines in tools.py. Claude picks a tool by its name and description, so
"is this stock undervalued" finds a door called that, where a generic valuation tool
with the same answer gets passed over (QUESTIONS.md; ~/.claude/CLAUDE.md "Every
question theme gets a tool", 2026-11-22).

Same rules as tools.py: no broker, no order, no model; numbers come from the engines.
"""
from __future__ import annotations

import pandas as pd

import load
import realized_pnl as rp

from . import inputs
from . import tools as T

TICKER, PRICE, BOND = T.TICKER, T.PRICE, T.BOND
TXN = {"type": "string", "description": "The transactions or gains/losses CSV from the broker, "
                                        "pasted as text (E*TRADE download, this repo's CSV, or "
                                        "plain columns date, action, symbol, quantity, price, fee)"}
POS = {"type": "string", "description": "The positions/portfolio CSV from the broker, pasted as "
                                        "text (symbol, quantity, market value or last price; a "
                                        "gain or cost-basis column for gains and losses)"}


def _door(name, title, desc, props, required, open_world, run):
    return {"name": name, "title": title, "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": required,
                            "additionalProperties": False},
            "annotations": T._ann(title, open_world)}, run


# ------------------------------------------------------------- valuation doors

def _undervalued(a):
    text, s, info = T.owner_earnings_valuation(a)
    return text, s, info


def _growth_priced_in(a):
    text, s, info = T.owner_earnings_valuation(a)
    if s and s.get("reverse_dcf"):
        head = ("Growth the price requires (reverse DCF, owner earnings for 10 years): " +
                ", ".join(f"{r['implied_owner_earnings_growth'] * 100:.1f}% a year at a "
                          f"{r['required_return_pct']}% return" for r in s["reverse_dcf"]
                          if r.get("implied_owner_earnings_growth") is not None) + ".")
        text = head + "\n\n" + text
    return text, s, info


def _cheapest(a):
    return T.compare_valuations(a)


def _flow_table(a, title, keys, derived):
    text, s, info = T.as_reported_financials({"ticker": a["ticker"], "years": a.get("years") or 10})
    v, fys = s["values"], s["fiscal_years"]
    rows = []
    for fy in fys:
        f = str(fy)
        row = {"fiscal_year": fy, **{k: v.get(k, {}).get(f) for k in keys}}
        row.update(derived(row))
        rows.append(row)
    cols = keys + [k for k in rows[0] if k not in keys and k != "fiscal_year"] if rows else keys
    lines = [f"**{s['name']} ({s['ticker']})**, {title}, fiscal years {fys[0]}-{fys[-1]}, "
             "as reported on annual filings ($M).", "",
             "| fiscal year | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for r in rows:
        lines.append(f"| {r['fiscal_year']} | " + " | ".join(
            "" if r.get(c) is None else "%.1f" % (r[c] / 1e6) for c in cols) + " |")
    lines += ["", "A blank cell: the filing doesn't carry that line for that year.",
              f"Filings: {s['sec_filings_url']}", T.NOT_ADVICE]
    return "\n".join(lines), {"ticker": s["ticker"], "name": s["name"], "rows": rows,
                              "xbrl_tags": s["xbrl_tags"], "filings": s["filings"],
                              "sec_filings_url": s["sec_filings_url"], "units": "USD"}, info


def _fcf(a):
    def d(r):
        cfo, cap = r.get("cfo"), r.get("capex")
        return {"free_cash_flow": None if cfo is None or cap is None else cfo - abs(cap)}
    return _flow_table(a, "free cash flow (operating cash flow - capital expenditure)",
                       ["cfo", "capex"], d)


def _shareholder_returns(a):
    def d(r):
        dv, bb = r.get("dividends_paid"), r.get("buybacks")
        tot = None if dv is None and bb is None else abs(dv or 0) + abs(bb or 0)
        cfo, cap = r.get("cfo"), r.get("capex")
        fcf = None if cfo is None or cap is None else cfo - abs(cap)
        return {"total_returned": tot, "free_cash_flow": fcf}
    return _flow_table(a, "cash returned to shareholders (dividends and buybacks)",
                       ["dividends_paid", "buybacks", "cfo", "capex"], d)


# ------------------------------------------------------------- broker-export doors

def _closed_lots(csv):
    raw, rep = inputs.transactions(csv)
    df = load.normalize(raw)
    closed, orphans = rp.realized(df)
    return df, closed, orphans, rep


def _short_long(a):
    df, closed, orphans, rep = _closed_lots(a["transactions_csv"])
    year = a.get("tax_year")
    if not len(closed):
        return (f"Read {rep['rows_read']} rows; no sell matched a buy, so there are no closed "
                "lots to split.", {"by_year": [], "read": rep}, {"n_rows": rep["rows_read"], "n_lots": 0})
    c = closed.copy()
    bd, sd = pd.to_datetime(c["buy_date"]), pd.to_datetime(c["sell_date"])
    c["term"] = ["long" if s > b + pd.DateOffset(years=1) else "short" for b, s in zip(bd, sd)]
    c["year"] = sd.dt.year
    if year:
        c = c[c["year"] == int(year)]
    out = []
    for y, g in c.groupby("year"):
        row = {"tax_year": int(y)}
        for term in ("short", "long"):
            t = g[g["term"] == term]
            row[term] = {"lots": int(len(t)), "gains": T._r(t.loc[t["gain"] > 0, "gain"].sum()),
                         "losses": T._r(t.loc[t["gain"] < 0, "gain"].sum()),
                         "net": T._r(t["gain"].sum())}
        out.append(row)
    lines = [f"Read {rep['rows_read']} rows ({rep['format']}). Closed lots matched first-in, "
             "first-out; long-term means held more than one year (sold after the purchase "
             "anniversary).", "",
             "| tax year | short-term net | (lots) | long-term net | (lots) |", "|---|---|---|---|---|"]
    for r in out:
        lines.append(f"| {r['tax_year']} | {T._usd(r['short']['net'])} | {r['short']['lots']} | "
                     f"{T._usd(r['long']['net'])} | {r['long']['lots']} |")
    if not out:
        lines.append(f"| {year} | no closed lots that year | | | |")
    if len(orphans):
        lines += ["", f"{len(orphans)} sell(s) have no buy in this export: their cost basis "
                      "predates it, so they are left out rather than counted as gains."]
    lines += ["", "Lots matched FIFO from this export only; the broker's 1099-B (specific-lot "
                  "choices, wash-sale adjustments, basis carried in) is the figure that files.",
              T.NOT_ADVICE]
    return "\n".join(lines), {"by_year": out, "sells_without_basis": int(len(orphans)), "read": rep}, \
        {"n_rows": rep["rows_read"], "n_lots": int(len(c))}


def _wash_sales(a):
    df, closed, orphans, rep = _closed_lots(a["transactions_csv"])
    buys = df[df["is_trade"] & df["transactionType"].eq("Bought")]
    hits = []
    losses = closed[closed["gain"] < 0] if len(closed) else closed
    for _, r in losses.iterrows():
        sd = pd.Timestamp(r["sell_date"])
        w = buys[buys["brokerage.displaySymbol"].eq(r["symbol"])]
        bd = pd.to_datetime(w["transactionDate"])
        near = w[(bd >= sd - pd.Timedelta(days=30)) & (bd <= sd + pd.Timedelta(days=30))
                 & (bd != pd.Timestamp(r["buy_date"]))]
        if len(near):
            hits.append({"symbol": r["symbol"], "sell_date": f"{sd:%Y-%m-%d}",
                         "qty_sold": T._r(r["qty"], 4), "loss": T._r(r["gain"]),
                         "repurchases": [{"date": f"{pd.Timestamp(x['transactionDate']):%Y-%m-%d}",
                                          "qty": T._r(abs(float(x['brokerage.quantity'])), 4),
                                          "price": T._r(float(x['brokerage.price']), 4)}
                                         for _, x in near.iterrows()]})
    lines = [f"Read {rep['rows_read']} rows. {len(losses)} closed lot(s) sold at a loss; "
             f"**{len(hits)} had the same symbol bought within 30 days before or after the sale.**"]
    for h in hits:
        lines.append(f"- {h['symbol']}: sold {h['qty_sold']} on {h['sell_date']} for a "
                     f"{T._usd(abs(h['loss']))} loss; bought again " +
                     ", ".join(f"{p['qty']} on {p['date']}" for p in h["repurchases"]))
    lines += ["", "A screen on this export: same ticker only (not 'substantially identical' "
                  "funds), this account only (not an IRA or a spouse's account). The broker's "
                  "1099-B carries the wash-sale adjustment that files.", T.NOT_ADVICE]
    return "\n".join(lines), {"loss_lots": int(len(losses)), "possible_wash_sales": hits, "read": rep}, \
        {"n_rows": rep["rows_read"], "n_lots": int(len(losses))}


def _harvest(a):
    pf, prep = inputs.positions(a["positions_csv"])
    if prep.get("no_gain_column"):
        raise T.ToolError("tax_loss_harvest_candidates: the positions CSV needs a gain column "
                          "(total gain) or a cost-basis column (total cost) to see a loss.")
    lo = pf[pf["totalGain"] < 0].sort_values("totalGain")
    recent = set()
    if a.get("transactions_csv"):
        raw, _ = inputs.transactions(a["transactions_csv"])
        df = load.normalize(raw)
        b = df[df["is_trade"] & df["transactionType"].eq("Bought")]
        last = pd.to_datetime(df["transactionDate"]).max()
        b = b[pd.to_datetime(b["transactionDate"]) >= last - pd.Timedelta(days=30)]
        recent = set(b["brokerage.displaySymbol"].astype(str).str.upper())
    rows = [{"symbol": r["symbol"], "quantity": T._r(r["quantity"], 4),
             "market_value": T._r(r["marketValue"]), "unrealized_loss": T._r(r["totalGain"]),
             "bought_in_last_30_days": (r["symbol"] in recent) if a.get("transactions_csv") else None}
            for _, r in lo.iterrows()]
    total = T._r(lo["totalGain"].sum())
    lines = [f"{len(pf)} positions read; **{len(rows)} sit at an unrealized loss, "
             f"{T._usd(total)} in all.**", "",
             "| symbol | market value | unrealized loss | bought in last 30 days |", "|---|---|---|---|"]
    for r in rows:
        flag = "not checked" if r["bought_in_last_30_days"] is None else (
            "yes" if r["bought_in_last_30_days"] else "no")
        lines.append(f"| {r['symbol']} | {T._usd(r['market_value'])} | {T._usd(r['unrealized_loss'])} | {flag} |")
    lines += ["", "Selling a position bought in the last 30 days, or buying it back within 30 days "
                  "of the sale, can make the loss a wash sale. A positions export has no "
                  "purchase dates per lot, so short- vs long-term isn't known here.", T.NOT_ADVICE]
    return "\n".join(lines), {"candidates": rows, "total_unrealized_loss": total, "read": prep}, \
        {"n_rows": int(len(pf)), "n_tickers": len(rows)}


def _win_rate(a):
    return T.realized_pnl(a)


def _no_stop(a):
    return T.stop_coverage_check(a)


# ------------------------------------------------------------- the doors

_spec = [
    _door("is_this_stock_undervalued", "Is This Stock Undervalued?",
          "Whether a company's share price sits above or below what its owner earnings support, "
          "from its SEC filings: value per share on three anchors, the price's gap to each, a "
          "margin-of-safety ladder, and the growth the price requires. Same engine as "
          "owner_earnings_valuation. Returns refused, with the reason, when the business has no "
          "durable owner earnings to value. "
          "Example questions: \"Is Coca-Cola undervalued at $70?\"; \"What's Deere's intrinsic "
          "value?\"; \"Is MSFT overvalued right now?\"",
          {"ticker": TICKER, "price": PRICE, "long_bond_pct": BOND,
           "hurdle_pct": {"type": "number", "minimum": 3, "maximum": 30,
                          "description": "Required return, % (default 10)"}},
          ["ticker", "price"], True, _undervalued),
    _door("growth_priced_into_stock", "Growth Priced Into a Stock",
          "The owner-earnings growth a share price requires over 10 years (a reverse DCF) at 8, "
          "9, 10 and 12% required returns, from the company's SEC filings, followed by the full "
          "owner-earnings valuation. The reading that does not depend on a chosen hurdle rate. "
          "Example questions: \"What growth is priced into Nvidia?\"; \"What does the market "
          "expect from Costco at $900?\"; \"Reverse DCF on KO\"",
          {"ticker": TICKER, "price": PRICE, "long_bond_pct": BOND}, ["ticker", "price"], True,
          _growth_priced_in),
    _door("which_stock_is_cheapest", "Which Stock Is Cheapest",
          "Ranks two or more companies by the gap between price and owner-earnings value, from "
          "their SEC filings, with the loss-makers and the unvaluable named rather than dropped. "
          "Same engine as compare_valuations. "
          "Example questions: \"Which is cheaper, Coke or Pepsi?\"; \"Rank AAPL, MSFT and GOOGL "
          "on value\"; \"Which of my stocks is most overvalued?\"",
          {"companies": None}, ["companies"], True, _cheapest),
    _door("free_cash_flow_history", "Free Cash Flow History",
          "A company's free cash flow by fiscal year (operating cash flow minus capital "
          "expenditure) with both inputs, as reported on its annual SEC filings, plus the filing "
          "links. "
          "Example questions: \"What's Apple's free cash flow?\"; \"Has Ford's free cash flow "
          "grown?\"; \"10 years of FCF for COST\"",
          {"ticker": TICKER, "years": {"type": "integer", "minimum": 3, "maximum": 15,
                                       "description": "Fiscal years of history (default 10)"}},
          ["ticker"], True, _fcf),
    _door("dividends_and_buybacks", "Dividends and Buybacks",
          "Cash a company returned to shareholders by fiscal year, dividends paid and shares "
          "repurchased, beside its free cash flow, as reported on its annual SEC filings. "
          "Example questions: \"How much does Apple spend on buybacks?\"; \"Can Verizon cover "
          "its dividend?\"; \"Dividend history for KO\"",
          {"ticker": TICKER, "years": {"type": "integer", "minimum": 3, "maximum": 15,
                                       "description": "Fiscal years of history (default 10)"}},
          ["ticker"], True, _shareholder_returns),
    _door("short_vs_long_term_capital_gains", "Short- vs Long-Term Capital Gains",
          "Realized gains and losses from a pasted broker transactions export, split into "
          "short-term (held a year or less) and long-term by tax year, lots matched first-in, "
          "first-out, with sells that have no buy in the export set aside. "
          "Example questions: \"How much short-term gain did I have this year?\"; \"Split my "
          "2025 trades into short and long term\"; \"What are my capital gains?\"",
          {"transactions_csv": TXN,
           "tax_year": {"type": "integer", "minimum": 1990, "maximum": 2100,
                        "description": "One tax year (default: every year in the export)"}},
          ["transactions_csv"], False, _short_long),
    _door("wash_sale_check", "Wash Sale Check",
          "Screens a pasted broker transactions export for losing sales with the same ticker "
          "bought within 30 days before or after, listing each sale, its loss and the "
          "repurchases. "
          "Example questions: \"Did I trigger any wash sales?\"; \"Is my TSLA loss a wash "
          "sale?\"; \"Check my trades for the 30-day rule\"",
          {"transactions_csv": TXN}, ["transactions_csv"], False, _wash_sales),
    _door("tax_loss_harvest_candidates", "Tax-Loss Harvest Candidates",
          "Positions sitting at an unrealized loss in a pasted positions export, largest loss "
          "first, with the total; given a transactions export too, flags any bought in the last "
          "30 days (wash-sale risk on a sale). "
          "Example questions: \"What can I tax-loss harvest?\"; \"Which of my positions are "
          "losers?\"; \"Any losses to offset my gains this year?\"",
          {"positions_csv": POS,
           "transactions_csv": {**TXN, "description": TXN["description"] + ". Optional: flags "
                                "recent buys for the 30-day rule"}},
          ["positions_csv"], False, _harvest),
    _door("trading_win_rate", "Trading Win Rate",
          "Win rate, average win, average loss, payoff ratio and average hold from a pasted "
          "broker transactions export, with the best and worst trades and the totals by symbol. "
          "Same engine as realized_pnl. "
          "Example questions: \"What's my win rate?\"; \"Am I a good trader?\"; \"What were my "
          "worst trades this year?\"",
          {"transactions_csv": TXN, "symbol": {"type": "string",
                                               "description": "One symbol only (optional)"}},
          ["transactions_csv"], False, _win_rate),
    _door("positions_without_stop_loss", "Positions Without a Stop Loss",
          "Which held positions have no working stop order covering them, from pasted positions "
          "and open-orders exports: each row critical, warning, ok or unchecked, with the reason. "
          "Same engine as stop_coverage_check. "
          "Example questions: \"Do all my positions have stop losses?\"; \"Which stocks am I "
          "holding with no stop?\"; \"Did my stop orders expire?\"",
          {"positions_csv": POS, "orders_csv": None}, ["positions_csv", "orders_csv"], False, _no_stop),
]


def _borrow(name, key):
    """Reuse an engine tool's own schema for a property, so the two can't drift."""
    return next(t for t in T.TOOLS if t["name"] == name)["inputSchema"]["properties"][key]


DOORS, IMPL = [], {}
for d, run in _spec:
    props = d["inputSchema"]["properties"]
    if "companies" in props:
        props["companies"] = _borrow("compare_valuations", "companies")
    if d["name"] == "positions_without_stop_loss":
        props["orders_csv"] = _borrow("stop_coverage_check", "orders_csv")
    DOORS.append(d)
    IMPL[d["name"]] = run
