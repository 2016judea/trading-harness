"""Pasted broker exports -> the frames the harness scripts already analyse.

The connector never talks to a broker. A person pastes their own export into the
chat; this maps it onto the column names `load.py`, `realized_pnl.py`,
`gate_check.py` and `rebalance.py` were written against, so a pasted CSV and a
pulled one go through the same rules.

Three shapes are read:
  * the CSVs this repo's fetchers write (E*TRADE API field names), unchanged;
  * E*TRADE's own web download (a preamble, then TransactionDate,
    TransactionType, Symbol, Quantity, Price, Commission...);
  * a generic set: date, action, symbol, quantity, price, fee (any order, any
    case, "$"/"#" decorations ignored), which most brokers' exports already
    resemble and which a model can produce from anything else.

Nothing here is stored. The text arrives, is parsed, and is dropped with the call.
"""
from __future__ import annotations

import csv
import io
import re

import pandas as pd

MARKET_TZ = "America/New_York"
MAX_CHARS = 2_000_000


class InputError(ValueError):
    """The pasted text can't be read as the table asked for. The message says why."""


def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


# Generic name -> accepted header spellings, after _key(). First match wins.
TX_COLS = {
    "date": ["transactiondate", "transaction date", "activity trade date", "trade date",
             "date", "run date", "tradedate", "date acquired", "settlement date"],
    "action": ["transactiontype", "transaction type", "activity type", "action", "side",
               "type", "buy sell", "trans code", "transaction"],
    "symbol": ["brokerage displaysymbol", "symbol", "ticker", "security", "instrument"],
    "quantity": ["brokerage quantity", "quantity", "qty", "shares", "units"],
    "price": ["brokerage price", "price", "execution price", "trade price", "fill price",
              "avg price"],
    "commission": ["brokerage fee", "commission", "commissions", "fees comm",
                   "commissions and fees", "fee", "fees"],
    "fees": ["fees", "other fees", "reg fees", "sec fee"],
    "amount": ["amount", "net amount"],
    "security_type": ["securitytype", "security type", "asset type", "asset class"],
    "id": ["transactionid", "transaction id", "id", "reference"],
}

POS_COLS = {
    "symbol": ["symbol", "ticker", "security"],
    "quantity": ["quantity", "qty", "shares", "units"],
    "last_price": ["lastprice", "last price", "price", "last", "current price",
                   "market price", "last price change"],
    "market_value": ["marketvalue", "market value", "value", "current value", "total value",
                     "mkt val", "market value total"],
    "total_gain": ["totalgain", "total gain", "gain loss", "unrealized gain",
                   "unrealized gain loss", "unrealized p l", "gain", "total gain loss"],
    "total_cost": ["totalcost", "cost basis", "cost basis total", "total cost", "cost"],
    "pct": ["pctofportfolio", "of portfolio", "percent of account", "pct of portfolio",
            "weight", "of account", "percent of portfolio"],
    "account": ["accountid", "account", "account number"],
}

ORD_COLS = {
    "order_id": ["orderid", "order id", "order number", "order", "id"],
    "symbol": ["symbol", "ticker", "security"],
    "side": ["orderaction", "order action", "action", "side", "buy sell", "transaction"],
    "price_type": ["pricetype", "price type", "order type", "type"],
    "quantity": ["orderedquantity", "ordered quantity", "quantity", "qty", "shares"],
    "filled": ["filledquantity", "filled quantity", "filled", "executed", "qty filled"],
    "limit": ["limitprice", "limit price", "limit"],
    "stop": ["stopprice", "stop price", "stop", "trigger price", "stop trigger",
             "activation price"],
    "price": ["price"],
    "term": ["orderterm", "order term", "term", "duration", "time in force", "tif",
             "expiration"],
    "status": ["status", "order status"],
    "replaces": ["replacesorderid", "linked order id", "linked to", "oco group",
                 "bracket id"],
    "replaced_by": ["replacedbyorderid"],
}


def _table(text: str, cols: dict, need: list[str], what: str) -> tuple[pd.DataFrame, dict, int]:
    """Find the header row (exports open with a preamble), read from it, and map
    columns. Returns (frame, {generic: header}, header_line_index)."""
    if not text or not text.strip():
        raise InputError(f"{what}: empty. Paste the CSV text itself.")
    if len(text) > MAX_CHARS:
        raise InputError(f"{what}: {len(text):,} characters, over the {MAX_CHARS:,} limit. "
                         "Split it by year or account.")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sep = "\t" if lines and lines[0].count("\t") > lines[0].count(",") else ","
    best = None
    for i, line in enumerate(lines[:60]):
        if not line.strip():
            continue
        try:
            fields = next(csv.reader([line], delimiter=sep))
        except Exception:
            continue
        mapping = _match(fields, cols)
        if all(k in mapping for k in need):
            best = (i, mapping)
            break
    if best is None:
        heads = ", ".join(sorted({a for k in need for a in cols[k][:2]}))
        raise InputError(f"{what}: no header row with the columns needed "
                         f"({', '.join(need)}). Accepted names include: {heads}.")
    i, mapping = best
    body = "\n".join(lines[i:])
    df = pd.read_csv(io.StringIO(body), sep=sep, dtype=str, skip_blank_lines=True,
                     on_bad_lines="skip", engine="python")
    df = df.dropna(how="all")
    return df, mapping, i


def _match(headers: list[str], cols: dict) -> dict:
    keyed = {}
    for h in headers:
        k = _key(h)
        if k and k not in keyed:
            keyed[k] = h
    out = {}
    used = set()
    for generic, aliases in cols.items():
        for a in aliases:
            if a in keyed and keyed[a] not in used:
                out[generic] = keyed[a]
                used.add(keyed[a])
                break
    return out


def _num(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.strip()
    neg = t.str.match(r"^\(.*\)$")
    t = t.str.replace(r"[,$%\s()]", "", regex=True).replace({"": None, "nan": None,
                                                             "--": None, "-": None,
                                                             "None": None})
    v = pd.to_numeric(t, errors="coerce")
    return v.where(~neg, -v)


DATE_FORMATS = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p",
                "%d-%b-%Y", "%b %d, %Y"]


def _dates(s: pd.Series) -> tuple[pd.Series, str]:
    """-> (epoch ms on the exchange's clock, the format that read most rows).
    A date with no time is midnight New York, so load.normalize's UTC->NY step
    lands it back on the same calendar day."""
    raw = s.astype(str).str.strip().str.replace(r"\s+as of .*$", "", regex=True, case=False)
    nums = pd.to_numeric(raw, errors="coerce")
    if nums.notna().mean() > 0.8:
        unit = "ms" if nums.dropna().median() > 1e11 else "s"
        ts = pd.to_datetime(nums, unit=unit, utc=True)
        return _ms(ts), f"epoch {unit}"
    best, fmt_used = None, None
    for fmt in DATE_FORMATS:
        p = pd.to_datetime(raw, format=fmt, errors="coerce")
        if best is None or p.notna().sum() > best.notna().sum():
            best, fmt_used = p, fmt
    if best is None or best.notna().sum() == 0:
        return pd.Series([None] * len(s), index=s.index, dtype="float64"), "unreadable"
    loc = best.dt.tz_localize(MARKET_TZ, ambiguous="NaT", nonexistent="shift_forward")
    return _ms(loc.dt.tz_convert("UTC")), fmt_used


def _ms(ts: pd.Series) -> pd.Series:
    """Epoch milliseconds, whatever the datetime resolution. pandas 3 keeps the
    unit it parsed (ms, us, s), so astype('int64') // 10**6 is only right for ns:
    on the first run it dated every 2024 trade to January 1970."""
    epoch = pd.Timestamp(0, tz="UTC")
    return ((ts - epoch) // pd.Timedelta(milliseconds=1)).astype("float64").where(ts.notna())


BUY_RE = re.compile(r"\b(?:BOUGHT|BUY|PURCHASE|PURCHASED|REINVEST(?:MENT|ED)? SHARES|"
                    r"REINVESTMENT|B)\b")
SELL_RE = re.compile(r"\b(?:SOLD|SELL|SALE|S)\b")
OPTION_RE = re.compile(r"\b(?:TO OPEN|TO CLOSE|OPTION|OPTN|CALL|PUT|ASSIGNED|EXERCISED)\b")


def transactions(text: str) -> tuple[pd.DataFrame, dict]:
    """A transactions export -> the frame load.normalize() takes, plus a report
    of how it was read: format, columns used, rows kept and rows set aside."""
    df, m, at = _table(text, TX_COLS, ["date", "action", "symbol", "quantity"],
                       "transactions")
    rep = {"header_line": at + 1, "columns_used": m, "rows_read": int(len(df))}
    native = {"transactionType", "brokerage.displaySymbol", "brokerage.quantity",
              "brokerage.price", "transactionDate"}.issubset(df.columns)
    rep["format"] = "trading-harness API CSV" if native else (
        "E*TRADE download" if {"TransactionDate", "TransactionType"}.issubset(df.columns)
        else "generic columns")

    action = df[m["action"]].astype(str).str.upper().str.replace(r"[^A-Z0-9]+", " ",
                                                                  regex=True).str.strip()
    qty = _num(df[m["quantity"]]).abs()
    price = _num(df[m["price"]]) if "price" in m else pd.Series([float("nan")] * len(df),
                                                                 index=df.index)
    fee = pd.Series(0.0, index=df.index)
    if "commission" in m:
        fee = fee + _num(df[m["commission"]]).fillna(0).abs()
    if "fees" in m and m.get("fees") != m.get("commission"):
        fee = fee + _num(df[m["fees"]]).fillna(0).abs()
    if "amount" in m and "price" not in m:
        # No price column: back it out of the cash amount. That folds the fee into
        # the price, so the fee is zeroed rather than counted twice.
        price = (_num(df[m["amount"]]).abs() / qty).where(qty > 0)
        fee = fee * 0
        rep["price_from_amount"] = True
    when, fmt = _dates(df[m["date"]])
    rep["date_format"] = fmt
    sec = df[m["security_type"]].astype(str).str.upper() if "security_type" in m else None
    sym = df[m["symbol"]].astype(str).str.strip().str.upper()

    is_opt = action.str.contains(OPTION_RE) | sym.str.contains(r"\s|\d{6}[CP]\d", regex=True)
    if sec is not None:
        is_opt = is_opt | sec.str.contains("OPT")
    side = pd.Series("other", index=df.index)
    side[action.str.contains(SELL_RE)] = "Sold"
    side[action.str.contains(BUY_RE) & ~action.str.contains(SELL_RE)] = "Bought"
    if native:
        side[action.eq("BOUGHT")] = "Bought"
        side[action.eq("SOLD")] = "Sold"
    trade = side.isin(["Bought", "Sold"])
    usable = trade & ~is_opt & qty.gt(0) & price.gt(0) & when.notna() & sym.ne("") & sym.ne("NAN")

    rep["options_rows_set_aside"] = int((trade & is_opt).sum())
    rep["trade_rows_unreadable"] = int((trade & ~is_opt & ~usable).sum())
    other = action[~trade]
    rep["non_trade_rows"] = int((~trade).sum())
    rep["non_trade_kinds"] = {k.title(): int(v) for k, v in other.value_counts().head(6).items()}

    out = pd.DataFrame({
        "transactionDate": when,
        "transactionType": side.where(usable, df[m["action"]].astype(str)),
        "brokerage.displaySymbol": sym,
        "brokerage.quantity": qty,
        "brokerage.price": price,
        "brokerage.fee": fee,
    })
    if native or "id" in m:
        out.insert(0, "transactionId", df[m.get("id", "transactionId")].values)
    out = out[usable | ~trade]
    out = out[out["transactionDate"].notna()]
    # Broker downloads list newest first. load.normalize sorts stably by date, so
    # same-day rows keep file order: reverse a newest-first file, or a same-day
    # sell lands before its buy and reads as a sell with no basis.
    t = out["transactionDate"].astype("float64")
    if len(t) > 1 and t.iloc[0] > t.iloc[-1]:
        out = out.iloc[::-1]
        rep["file_order"] = "newest first (reversed)"
    rep["trades_used"] = int(out["transactionType"].isin(["Bought", "Sold"]).sum())
    if not rep["trades_used"]:
        raise InputError("transactions: no buy or sell rows with a date, symbol, quantity and "
                         f"price. Actions seen: {', '.join(list(rep['non_trade_kinds'])[:6]) or 'none'}.")
    return out.reset_index(drop=True), rep


SKIP_SYMBOLS = re.compile(r"^(CASH|TOTAL|ACCOUNT TOTAL|PENDING|NAN|--|CASH & CASH INVESTMENTS|"
                          r"CASH AND MONEY MARKET|MONEY MARKET)", re.I)


def positions(text: str) -> tuple[pd.DataFrame, dict]:
    """A positions export -> gate_check/rebalance's positions frame (symbol,
    quantity, lastPrice, marketValue, totalGain, pctOfPortfolio)."""
    df, m, at = _table(text, POS_COLS, ["symbol", "quantity"], "positions")
    rep = {"header_line": at + 1, "columns_used": m, "rows_read": int(len(df))}
    sym = df[m["symbol"]].astype(str).str.strip().str.upper()
    qty = _num(df[m["quantity"]])
    last = _num(df[m["last_price"]]) if "last_price" in m else None
    mv = _num(df[m["market_value"]]) if "market_value" in m else None
    cash_rows = sym.str.match(r"^(CASH|MONEY MARKET)", case=False)
    cash = float(mv[cash_rows].sum()) if mv is not None and cash_rows.any() else None
    keep = ~sym.str.match(SKIP_SYMBOLS) & qty.notna() & (qty != 0) & sym.ne("")
    if mv is None and last is None:
        raise InputError("positions: needs a market value column or a last price column "
                         "to size each position.")
    if mv is None:
        mv = qty * last
        rep["market_value_from"] = "quantity x last price"
    if last is None:
        last = (mv / qty).where(qty != 0)
        rep["last_price_from"] = "market value / quantity"
    gain = _num(df[m["total_gain"]]) if "total_gain" in m else None
    if gain is None and "total_cost" in m:
        gain = mv - _num(df[m["total_cost"]])
        rep["gain_from"] = "market value - cost basis"
    out = pd.DataFrame({
        "symbol": sym, "quantity": qty, "lastPrice": last, "marketValue": mv,
        "totalGain": gain if gain is not None else float("nan"),
    })[keep].reset_index(drop=True)
    if not len(out):
        raise InputError("positions: no rows with a symbol and a non-zero quantity.")
    rep["positions"] = int(len(out))
    rep["cash_row"] = cash
    if gain is None:
        rep["no_gain_column"] = True
    return out, rep


def with_pct(pf: pd.DataFrame, cash: float | None) -> pd.DataFrame:
    """pctOfPortfolio over positions plus cash, the way the broker reports it."""
    pf = pf.copy()
    denom = float(pf["marketValue"].sum()) + float(cash or 0)
    pf["pctOfPortfolio"] = 100 * pf["marketValue"] / denom if denom else 0.0
    return pf


def _norm_side(v: str) -> str:
    v = re.sub(r"[^A-Z0-9]+", " ", str(v).upper()).strip()
    if "SHORT" in v and "COVER" not in v:
        return "SELL_SHORT"
    if re.search(r"\b(SELL|SOLD|S)\b", v):
        return "SELL"
    if re.search(r"\b(BUY|BOUGHT|B|COVER)\b", v):
        return "BUY"
    return v or "UNKNOWN"


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and v != v) or _key(v) in ("", "nan", "none")


def _norm_term(v) -> str:
    if _blank(v):
        return "UNKNOWN"
    v = _key(v)
    if not v:
        return "UNKNOWN"
    if "good for day" in v or v in ("day", "gfd", "good for day") or v.startswith("day"):
        return "GOOD_FOR_DAY"
    if "gtc" in v or "until cancel" in v or "til cancel" in v or "till cancel" in v:
        return "GOOD_UNTIL_CANCEL"
    if "gtd" in v or "till date" in v or "until date" in v:
        return "GOOD_TILL_DATE"
    if v in ("ioc", "immediate or cancel"):
        return "IMMEDIATE_OR_CANCEL"
    if v in ("fok", "fill or kill"):
        return "FILL_OR_KILL"
    return v.upper().replace(" ", "_")


def _norm_status(v) -> str:
    if _blank(v):
        return "OPEN"
    v = _key(v)
    if "partial" in v:
        return "PARTIAL"
    if "cancel req" in v or "cancel pending" in v or "pending cancel" in v:
        return "CANCEL_REQUESTED"
    if any(w in v for w in ("open", "working", "pending", "accepted", "live", "received")):
        return "OPEN"
    if "cancel" in v:
        return "CANCELLED"
    if "expire" in v:
        return "EXPIRED"
    if "execut" in v or "filled" in v:
        return "EXECUTED"
    if "reject" in v:
        return "REJECTED"
    return v.upper().replace(" ", "_")


def _norm_type(v) -> str:
    v = "" if _blank(v) else _key(v)
    if "trail" in v:
        return "TRAILING_STOP_CNST"
    if "stop" in v and "limit" in v:
        return "STOP_LIMIT"
    if "stop" in v:
        return "STOP"
    if "limit" in v:
        return "LIMIT"
    if "market" in v:
        return "MARKET"
    return v.upper().replace(" ", "_") or "UNKNOWN"


def orders(text: str) -> tuple[pd.DataFrame, dict]:
    """Resting orders -> gate_check's orders frame. A list with no status column
    is read as open orders, and a list with no term column can't show a
    good-for-day order: both are said in the report, not assumed silently."""
    df, m, at = _table(text, ORD_COLS, ["symbol", "side", "quantity"], "orders")
    rep = {"header_line": at + 1, "columns_used": m, "rows_read": int(len(df))}
    n = len(df)
    col = lambda k: df[m[k]] if k in m else pd.Series([None] * n, index=df.index)
    ptype = col("price_type").map(_norm_type) if "price_type" in m else pd.Series("UNKNOWN", index=df.index)
    limit, stop = _num(col("limit")), _num(col("stop"))
    if "price" in m:
        px = _num(df[m["price"]])
        stop = stop.where(stop.notna() | ~ptype.isin(["STOP", "STOP_LIMIT"]), px)
        limit = limit.where(limit.notna() | ~ptype.isin(["LIMIT"]), px)
    if "price_type" not in m:
        ptype = pd.Series("UNKNOWN", index=df.index)
        ptype[stop.notna() & limit.notna()] = "STOP_LIMIT"
        ptype[stop.notna() & limit.isna()] = "STOP"
        ptype[stop.isna() & limit.notna()] = "LIMIT"
    out = pd.DataFrame({
        "orderId": col("order_id").fillna("").astype(str).where(col("order_id").notna(),
                                                               [f"row{i + 1}" for i in range(n)]),
        "symbol": col("symbol").astype(str).str.strip().str.upper(),
        "orderAction": col("side").map(_norm_side),
        "priceType": ptype,
        "orderedQuantity": _num(col("quantity")).abs(),
        "filledQuantity": _num(col("filled")).fillna(0).abs(),
        "limitPrice": limit, "stopPrice": stop,
        "orderTerm": col("term").map(_norm_term),
        "status": col("status").map(_norm_status),
        "replacesOrderId": col("replaces").replace({"": None}),
        "replacedByOrderId": col("replaced_by").replace({"": None}),
    })
    out = out[out["symbol"].ne("") & out["symbol"].ne("NAN") & out["orderedQuantity"].gt(0)]
    if "status" not in m:
        rep["status_assumed"] = "no status column: every row read as OPEN"
    if "term" not in m:
        rep["term_missing"] = ("no term/duration column: a good-for-day order can't be told "
                               "from a good-until-cancelled one")
    rep["orders"] = int(len(out))
    return out.reset_index(drop=True), rep
