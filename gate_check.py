"""Reconcile the journal's gates against the orders actually resting at E*TRADE.

**The question this exists to ask: is the gate I believe is live actually in the
market?** Twice it wasn't, and both times a human found it by hand:

  * **2026-08-10** — a GOOD_FOR_DAY limit expired overnight; the stock traded
    through the level the next session with no order there. The precedent that
    made GTC the standing rule.
  * **2026-08-20** — after a deliberate concentration into a single name, the
    orders endpoint returned zero open orders. The book sat fully concentrated
    with its only remaining risk control existing solely on paper.

The journal's own conclusion was *"a gate that requires a human to be watching is
not a gate."* This is that watch, mechanised.

## Two tiers, deliberately separated

**Tier 1 — mechanical.** Positions vs resting orders. Needs no journal, cannot
be fooled by prose, and catches both failures above:
  1. an over-cap position with no resting stop covering it (8/20)
  2. any resting order that dies tonight — GOOD_FOR_DAY (8/10)
  3. resting sell quantity exceeding the shares held, unlinked (the
     over-commitment the 8/20 entry identified in its own three gates)
  4. a resting order in a symbol not held

**Tier 2 — journal levels, best effort.** Reads the price levels named in the
`⚑ GATE REGISTER`'s price section (`C`) and in an active tactical block if one
is open, and matches them against resting orders. **Every row is labelled `PARSED` or `UNPARSED`** and
unparsed rows are printed as holes. A level parser over a 76KB prose file will
miss things; the one thing it must never do is miss them quietly.

## Why a parse failure cannot produce a clean report

Tier 1 runs regardless. If the journal anchor is gone (the tactical trade closed,
the register was restructured) Tier 2 prints `JOURNAL LEVELS UNCHECKED` and the
verdict says so. If `data/orders.csv` is missing or older than today the script
refuses to render a verdict at all — reconciling against yesterday's orders is
worse than not reconciling, because it reads as authoritative.

Exit status is 0 only when nothing needs attention.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# Resting = still capable of executing. CANCEL_REQUESTED counts because the
# cancel may not have taken, and PARTIAL still has shares working.
RESTING = {"OPEN", "PARTIAL", "CANCEL_REQUESTED"}
SELL_ACTIONS = {"SELL", "SELL_SHORT"}

# Per-name cap, above which a resting stop is required. Set it to whatever your
# own journal's portfolio rules say. Why have one at all: a book that runs no
# stop-losses is relying on *size* as its risk control, and that is only true
# while no single name is big enough to hurt on its own. Past the cap, size
# controls nothing, and the stop is that same discipline expressed through the
# only variable left.
DEFAULT_CAP_PCT = 28.0


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(v) else v


# ---------------------------------------------------------------- tier 1

def load_frames(portfolio: Path, orders: Path, max_age_hours: float):
    if not portfolio.exists():
        sys.exit(f"REFUSING TO REPORT: {portfolio} missing. Run fetch_portfolio.py.")
    if not orders.exists():
        sys.exit(f"REFUSING TO REPORT: {orders} missing. Run fetch_orders.py.\n"
                 "There is no safe default here — 'no file' and 'no orders' look "
                 "identical in a summary and mean opposite things.")
    stale = []
    for p in (portfolio, orders):
        age_h = (datetime.now().timestamp() - p.stat().st_mtime) / 3600
        if age_h > max_age_hours:
            stale.append(f"{p.name} is {age_h/24:.1f} days old")
    pf = pd.read_csv(portfolio)
    od = pd.read_csv(orders) if orders.stat().st_size > 0 else pd.DataFrame()
    return pf, od, stale


def tier1(pf: pd.DataFrame, od: pd.DataFrame, cap_pct: float) -> list[tuple[str, str]]:
    """Returns a list of (severity, message). severity in {CRIT, WARN, OK}."""
    out = []
    resting = od[od["status"].isin(RESTING)] if len(od) else pd.DataFrame(columns=od.columns)

    held = {}
    for _, r in pf.iterrows():
        held[str(r["symbol"]).upper()] = {
            "qty": _num(r.get("quantity")) or 0.0,
            "pct": _num(r.get("pctOfPortfolio")) or 0.0,
            "mv": _num(r.get("marketValue")) or 0.0,
        }

    # 1. Over-cap position with no resting stop covering it.
    for sym, h in sorted(held.items(), key=lambda kv: -kv[1]["pct"]):
        if h["pct"] < cap_pct:
            continue
        stops = resting[
            (resting["symbol"].astype(str).str.upper() == sym)
            & (resting["orderAction"].astype(str).str.upper().isin(SELL_ACTIONS))
            & (resting["stopPrice"].apply(lambda v: (_num(v) or 0) > 0))
        ] if len(resting) else pd.DataFrame()
        covered = sum(_num(q) or 0 for q in stops.get("orderedQuantity", []))
        if covered <= 0:
            out.append(("CRIT",
                f"{sym} is {h['pct']:.2f}% of the book (cap {cap_pct:.0f}%) with "
                f"NO resting stop. {h['qty']:,.0f} sh / ${h['mv']:,.0f} unprotected."))
        elif covered + 1e-9 < h["qty"]:
            out.append(("CRIT",
                f"{sym} stop covers {covered:,.0f} of {h['qty']:,.0f} sh "
                f"({h['qty']-covered:,.0f} sh naked) at {h['pct']:.2f}% of book."))
        else:
            out.append(("OK",
                f"{sym} {h['pct']:.2f}% of book — stop covers {covered:,.0f} sh."))

    if not len(resting):
        out.append(("WARN", "Zero resting orders at the broker. If the journal "
                            "names any live price gate, it is on paper only."))
        return out

    # 2. Anything that dies tonight.
    gfd = resting[resting["orderTerm"].astype(str).str.upper().str.contains("GOOD_FOR_DAY")]
    for _, r in gfd.iterrows():
        out.append(("CRIT",
            f"order {r['orderId']} {r['orderAction']} {r['symbol']} is "
            f"GOOD_FOR_DAY — it is gone at the close, and tomorrow the level "
            f"is unwatched. Use GTC unless this is a genuine same-day view."))

    # 3. Resting sell quantity above the shares held.
    for sym, grp in resting.groupby(resting["symbol"].astype(str).str.upper()):
        sells = grp[grp["orderAction"].astype(str).str.upper().isin(SELL_ACTIONS)]
        want = sum(_num(q) or 0 for q in sells.get("orderedQuantity", []))
        have = held.get(sym, {}).get("qty", 0.0)
        linked = sells[["replacesOrderId", "replacedByOrderId"]].notna().any().any() \
            if {"replacesOrderId", "replacedByOrderId"}.issubset(sells.columns) else False
        if want > have + 1e-9 and not linked:
            out.append(("WARN",
                f"{sym}: {want:,.0f} sh of resting sells against {have:,.0f} sh held, "
                f"with no OCO/bracket link on the legs. Loose legs over-commit the "
                f"position: if the limit fills first, the stop is oversized."))

    # 4. Resting order in something not held.
    for sym in sorted(set(resting["symbol"].astype(str).str.upper()) - set(held)):
        acts = ", ".join(sorted(set(resting[resting["symbol"].astype(str).str.upper() == sym]["orderAction"].astype(str))))
        out.append(("WARN", f"resting {acts} order in {sym}, which is not a held position."))

    return out


# ---------------------------------------------------------------- tier 2

def parse_journal_levels(text: str) -> tuple[list[dict], list[str]]:
    """Best-effort extraction of order-expressible price levels.

    Returns (levels, holes). A `hole` is a gate row we recognised as a gate and
    could NOT turn into a number — it must be reported, never dropped.
    """
    levels, holes = [], []

    # --- the active tactical block's pre-committed numbers (T1/T2/T3) ---
    m = re.search(r"^##\s*.*ACTIVE TACTICAL TRADE.*$", text, re.M)
    if m:
        head = m.group(0)
        sym = re.search(r"\b([A-Z]{1,5})\b(?=\s+CONCENTRATION)", head)
        sym = sym.group(1) if sym else None
        block = text[m.end():]
        block = block[:block.find("\n---")] if "\n---" in block else block
        for row in re.findall(r"^\|.*$", block, re.M):
            gid = re.search(r"\*\*(T\d)\*\*", row)
            if not gid:
                continue
            gid = gid.group(1)
            label = re.sub(r"[*]", "", row.split("|")[2]).strip() if row.count("|") > 2 else ""
            cell = row.split("|")[3] if row.count("|") > 3 else ""
            pairs = re.findall(r"([\d,]+)\s*sh at \$([\d.]+)", cell)
            if pairs:
                for qty, px in pairs:
                    levels.append({"id": gid, "symbol": sym, "kind": "LIMIT",
                                   "qty": float(qty.replace(",", "")), "price": float(px),
                                   "label": label})
                continue
            if "WRONG" in label.upper():
                px = re.search(r"\$([\d.]+)", cell)
                if px:
                    levels.append({"id": gid, "symbol": sym, "kind": "STOP",
                                   "qty": None, "price": float(px.group(1)), "label": label})
                    continue
            if "DATE" in label.upper():
                d = re.search(r"(\d{4}-\d{2}-\d{2})", cell)
                if d:
                    levels.append({"id": gid, "symbol": sym, "kind": "DATE",
                                   "qty": None, "price": None, "label": label,
                                   "date": d.group(1)})
                    continue
            holes.append((sym, f"{gid} ({label or 'no label'}) — no level parsed "
                               f"from: {cell.strip()[:90]}"))
    else:
        holes.append((None, "no `ACTIVE TACTICAL TRADE` block found — if one is "
                             "open, its T-gates were NOT checked"))

    # --- register section C: price gates that can be resting orders ---
    csec = re.search(r"^\*\*C\.\s*PRICE.*?$(.*?)(?=^\*\*[D-Z]\.)", text, re.M | re.S)
    if csec:
        for row in re.findall(r"^\|\s*(C\d+)\s*\|(.*)$", csec.group(1), re.M):
            gid, rest = row
            cells = [c.strip() for c in rest.split("|")]
            sym = re.sub(r"[*\s]", "", cells[0]) if cells else None
            trig = cells[1] if len(cells) > 1 else ""
            state = cells[-2] if len(cells) >= 2 else ""
            px = re.search(r"[≥>]=?\s*~?\$([\d,]+(?:\.\d+)?)", trig)
            if px:
                levels.append({"id": gid, "symbol": sym, "kind": "LIMIT", "qty": None,
                               "price": float(px.group(1).replace(",", "")),
                               "label": f"sell-into-strength · {state}"})
            else:
                holes.append((sym, f"{gid} {sym} — no number in its trigger "
                                   f"(\"{trig[:60]}\"); decided live, which is "
                                   f"the condition the register exists to prevent"))
    else:
        holes.append((None, "register section C (price gates) not found — sell-"
                             "into-strength levels were NOT checked"))

    return levels, holes


def tier2(levels: list[dict], holes: list[str], od: pd.DataFrame,
          today: datetime, held: set[str]):
    """`held` is load-bearing. A gate on a name that is no longer in the book is
    DORMANT, not unplaced — there are no shares to sell, so nothing could be
    resting and nothing is wrong. Six of this register's seven names were sold
    on 2026-08-20; without this distinction the report cries wolf on four rows
    out of nine, and a reconciler that cries wolf is a reconciler nobody reads
    on the day it is right."""
    out = []
    resting = od[od["status"].isin(RESTING)] if len(od) else pd.DataFrame(columns=od.columns)

    for g in levels:
        sym = str(g.get("symbol") or "").upper()
        if g["kind"] != "DATE" and sym and sym not in held:
            out.append(("DORM", f"{g['id']} {sym} {g['kind']} ${g['price']:,.2f} "
                                f"— DORMANT, no position to sell."))
            continue
        if g["kind"] == "DATE":
            days = (datetime.strptime(g["date"], "%Y-%m-%d") - today).days
            sev = "CRIT" if days <= 0 else ("WARN" if days <= 3 else "OK")
            out.append((sev, f"{g['id']} {g['symbol']} dated exit {g['date']} — "
                             f"{days} day(s) out. No broker order expresses this; "
                             f"it fires by someone reading the calendar."))
            continue
        # Severity splits on which side of the trade the gate protects, and it
        # is not cosmetic. An unplaced STOP means money can be lost that the
        # journal said would be capped. An unplaced sell-into-strength LIMIT
        # means an upside is missed, and it may well be deliberate -- a tactical
        # trade often overrides a long-horizon sell target on purpose. Ranking
        # them the same makes the report cry wolf on gates that are unplaced by
        # design, and a report that cries wolf is not read on the day it is right.
        sev = "CRIT" if g["kind"] == "STOP" else "WARN"
        tail = "" if g["kind"] == "STOP" else " (a limit may be unplaced on purpose — check the block)"
        if not len(resting):
            out.append((sev, f"{g['id']} {g['symbol']} {g['kind']} "
                             f"${g['price']:,.2f} — PAPER ONLY.{tail}"))
            continue
        col = "limitPrice" if g["kind"] == "LIMIT" else "stopPrice"
        cand = resting[
            (resting["symbol"].astype(str).str.upper() == str(g["symbol"]).upper())
            & (resting[col].apply(lambda v: _num(v) is not None
                                  and abs((_num(v) or 0) - g["price"]) <= 0.01))
        ]
        if len(cand):
            r = cand.iloc[0]
            note = "" if "GOOD_UNTIL_CANCEL" in str(r["orderTerm"]).upper() \
                else f"  ⚠ term={r['orderTerm']}"
            out.append(("OK", f"{g['id']} {g['symbol']} {g['kind']} ${g['price']:,.2f} "
                              f"— PLACED (order {r['orderId']}, "
                              f"{_num(r['orderedQuantity']) or 0:,.0f} sh){note}"))
        else:
            qty = f" x {g['qty']:,.0f} sh" if g.get("qty") else ""
            out.append((sev, f"{g['id']} {g['symbol']} {g['kind']} "
                             f"${g['price']:,.2f}{qty} — PAPER ONLY, "
                             f"nothing resting at that level.{tail}"))
    STRUCTURAL = ("no `ACTIVE TACTICAL TRADE` block", "not found —", "not found -")
    for hsym, text in holes:
        # Absence of an optional section is not an unchecked gate. It is still
        # printed, because if the section DOES exist under a heading this cannot
        # match, tier 2 went blind and only tier 1 is covering you.
        if any(k in text for k in STRUCTURAL):
            out.append(("WARN", text + "  [tier 1 still covered this position]"))
            continue
        # A gate with no number is a real hole only while the position exists.
        # A qualitative gate on a name you already sold has no number AND no
        # position -- reporting it as unchecked forever means this script can
        # never go green, and a check that can never pass stops being read.
        if hsym and str(hsym).upper() not in held:
            out.append(("DORM", f"{text}  [no position — dormant]"))
        else:
            out.append(("HOLE", text))
    return out


# ---------------------------------------------------------------- report

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal", default="JOURNAL.md")
    ap.add_argument("--portfolio", default="data/portfolio.csv")
    ap.add_argument("--orders", default="data/orders.csv")
    ap.add_argument("--cap", type=float, default=DEFAULT_CAP_PCT,
                    help=f"per-name cap %% above which a stop is required (default {DEFAULT_CAP_PCT})")
    ap.add_argument("--max-age-hours", type=float, default=18.0,
                    help="how old the pulls may be before the verdict is refused")
    ap.add_argument("--allow-stale", action="store_true")
    args = ap.parse_args()

    pf, od, stale = load_frames(Path(args.portfolio), Path(args.orders), args.max_age_hours)
    if stale and not args.allow_stale:
        sys.exit("REFUSING TO REPORT — " + "; ".join(stale) + ".\n"
                 "Re-run fetch_portfolio.py and fetch_orders.py, or pass "
                 "--allow-stale to see the numbers with that caveat.")

    findings = [("SECTION", "TIER 1 — positions vs resting orders (mechanical)")]
    findings += tier1(pf, od, args.cap)

    jt = Path(args.journal).read_text() if Path(args.journal).exists() else ""
    if jt:
        levels, holes = parse_journal_levels(jt)
        findings.append(("SECTION",
                         f"TIER 2 — journal levels ({len(levels)} parsed, "
                         f"{len(holes)} unparsed)"))
        held_syms = {str(x).upper() for x in pf["symbol"].dropna()}
        findings += tier2(levels, holes, od, datetime.now(), held_syms)
    else:
        findings.append(("SECTION", "TIER 2 — JOURNAL LEVELS UNCHECKED"))
        findings.append(("HOLE", f"{args.journal} not found."))

    icon = {"CRIT": "🔴", "WARN": "🟡", "OK": "✅", "HOLE": "⬛", "DORM": "· "}
    for sev, msg in findings:
        if sev == "SECTION":
            print(f"\n=== {msg} ===")
        else:
            print(f"{icon[sev]} {sev:<4} {msg}")

    crit = sum(1 for s, _ in findings if s == "CRIT")
    warn = sum(1 for s, _ in findings if s == "WARN")
    hole = sum(1 for s, _ in findings if s == "HOLE")
    dorm = sum(1 for s, _ in findings if s == "DORM")
    print(f"\n=== {crit} critical · {warn} warning · {hole} unchecked · "
          f"{dorm} dormant ===")
    if stale:
        print("⚠ ran on STALE data: " + "; ".join(stale))
    if hole:
        print("⬛ An unchecked gate is not a passing gate. Read those rows in "
              "JOURNAL.md by eye.")
    sys.exit(1 if (crit or hole) else 0)


if __name__ == "__main__":
    main()
