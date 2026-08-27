"""Transmit a PRE-COMMITTED exit to E*TRADE, with rails.

**Scope, stated narrowly so it doesn't drift.** The premise of the journal in
this repo is that exit levels are decided in writing *before* conviction is
tested. This script is the transmitter for levels already written there. It is
**not** a trading bot, it decides nothing, and it has no opinion about price.
Its only job is to close the gap between "the level is written down" and "the
level is in the market."

That gap is not hypothetical. In the book this tooling was built for it opened
three separate times:

  * **2026-08-10** — a GOOD_FOR_DAY limit expired overnight; the stock traded
    through the level next session with nothing resting.
  * **2026-08-20** — a deliberate concentration into a single name went in with
    zero resting orders. The entry note itself then wrote the rule: *any future
    concentration above the cap places its stop in the same session as the entry.*
  * **2026-08-26** — six days later the stop was **still** unplaced. The rule had
    been written and then not executed, and `gate_check.py` — added three days
    earlier to catch exactly this — had never once been run.

The conclusion those three earned: *a gate that requires a human to be watching
is not a gate.* `gate_check.py` **detects** an unplaced gate; this **places** it.
Neither is useful without the other.

---

## ⚠️ E*TRADE'S API HAS NO OCO. Plan around it, don't discover it.

Verified against the order API reference 2026-08-26. `priceType` covers MARKET /
LIMIT / STOP / STOP_LIMIT / trailing / hidden variants; `orderTerm` covers
GOOD_UNTIL_CANCEL / GOOD_FOR_DAY / GOOD_TILL_DATE / IMMEDIATE_OR_CANCEL /
FILL_OR_KILL; the only linkage fields are `conditionType` (`CONTINGENT_GTE` /
`CONTINGENT_LTE`) against **another symbol's** price. **Nothing links two orders
on the same symbol so that one cancels the other.** OCO is a Power-E*TRADE UI
construct, not an API capability.

**So a bracket written as "976 sh, limit $88 / stop $75, one-cancels-other" is
not placeable through this API at all** — a fact worth knowing before you write
a plan that assumes it. The only expressible form is sequential:

    1. STOP  on ALL shares, GTC   <- the only risk control; ALWAYS first
    2. LIMIT on tranche 1, GTC
    3. LIMIT on tranche 2, GTC

**This is over-committed on purpose** — a full-size stop plus full-size limits
against one holding — and it is safe *only* because the two sides straddle the
last price and cannot both print. **The cost is that any fill leaves a stale,
oversized leg on the other side.** `reconcile` finds those. Run it the session
after any fill. *A workaround written down without its cleanup step attached
becomes the next silent failure.*

---

## The rails, and the reason for each

Money moves here, so the rules are explicit rather than tasteful, and each one
traces to something that actually goes wrong rather than an imagined hazard.

  **R1  Preview is mandatory.** Every place is preceded by a preview whose
        `previewId` is echoed in the place body. E*TRADE requires it; printing it
        also means a rejected order fails loudly instead of silently.
  **R2  SELL-only unless `--i-am-buying`.** A sell reduces risk; a buy spends
        money. On a margin account `marginBuyingPower` reads roughly twice the
        real cash, and a book that runs no stop-losses is relying on *size* as its
        only risk control — an automated buy is the one thing that can break
        sizing with no human in the loop.
  **R3  Never sell more than is held.** Checked against the live position, never
        a cached CSV: a stale file would approve a sell for shares sold this
        morning.
  **R4  Resting + new must not over-commit, per side.** Same-side sells may not
        exceed shares held. A stop and a limit *may* overlap — that is the no-OCO
        workaround above — but only when they straddle the last price.
  **R5  A sell STOP must be BELOW last; a sell LIMIT must be ABOVE.** Inverted,
        either becomes an immediate market order. The API accepts this without
        complaint, which makes it the most expensive typo available.
  **R6  Refuse anything more than 35% from last.** R5 alone would happily accept
        a $7.50 stop meant as $75.00 — it *is* below last. R6 is the decimal catch.
  **R7  GTC by default; GOOD_FOR_DAY on a stop refused without --force-day.**
        Direct descendant of the 2026-08-10 failure above.
  **R8  Dry run by default.** Nothing transmits without `--place`.
  **R9  Every transmitted order is appended to data/placed_orders.log** with
        timestamp, payload and broker response. A placement should never be
        something only a terminal scrollback remembers.
  **R10 MARKET is refused outright.** Scope, not safety: an *entry* is a decision
        and belongs to a human looking at a quote. Worth stating because the
        2026-08-20 entry above was a market order that filled at 92% of that day's
        range, a quarter off the high, and closed the session underwater. This
        script transmits pre-written *exits* only.

## Usage

    python place_order.py show                     # held vs resting
    python place_order.py from-journal             # dry-run the written plan
    python place_order.py from-journal --place     # transmit
    python place_order.py reconcile                # stale legs after a fill
    python place_order.py cancel 281

    # one-off, still railed:
    python place_order.py order ACME SELL 1000 STOP --stop 75.00
    python place_order.py order ACME SELL 500 LIMIT --limit 88.00 --place
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import etrade

HERE = Path(__file__).parent
JOURNAL = HERE / "JOURNAL.md"
TEMPLATE = HERE / "JOURNAL.template.md"
LOG = HERE / "data" / "placed_orders.log"

SANITY_PCT = 35.0          # R6
SELL_ACTIONS = {"SELL", "SELL_SHORT"}
RESTING = {"OPEN", "PARTIAL", "CANCEL_REQUESTED"}
TERMS = {"GTC": "GOOD_UNTIL_CANCEL", "DAY": "GOOD_FOR_DAY"}


# ---------------------------------------------------------------- broker reads

def _account() -> dict:
    return etrade.list_accounts()[0]


def positions(acct: dict) -> dict[str, dict]:
    """symbol -> {qty, last}. Live, never from a CSV — see R3."""
    out: dict[str, dict] = {}
    data = etrade.get(f"/v1/accounts/{acct['accountIdKey']}/portfolio")
    for acc in (data.get("PortfolioResponse", {}).get("AccountPortfolio") or []):
        for p in acc.get("Position") or []:
            sym = (p.get("Product") or {}).get("symbol")
            if sym:
                out[sym] = {"qty": float(p.get("quantity", 0)),
                            "last": float((p.get("Quick") or {}).get("lastTrade", 0) or 0)}
    return out


def resting(acct: dict) -> list[dict]:
    """Flatten every still-executable order leg."""
    out = []
    data = etrade.get(f"/v1/accounts/{acct['accountIdKey']}/orders", {"count": "100"})
    for o in (data.get("OrdersResponse", {}).get("Order") or []):
        for d in o.get("OrderDetail") or []:
            if d.get("status") not in RESTING:
                continue
            for ins in d.get("Instrument") or []:
                out.append({
                    "orderId": o.get("orderId"),
                    "symbol": (ins.get("Product") or {}).get("symbol"),
                    "action": ins.get("orderAction"),
                    "qty": float(ins.get("orderedQuantity", 0)) - float(ins.get("filledQuantity", 0)),
                    "priceType": d.get("priceType"),
                    "limitPrice": d.get("limitPrice"),
                    "stopPrice": d.get("stopPrice"),
                    "orderTerm": d.get("orderTerm"),
                    "status": d.get("status"),
                })
    return out


def last_price(symbol: str) -> float:
    q = etrade.get(f"/v1/market/quote/{symbol}", {"detailFlag": "ALL"})
    return float(q["QuoteResponse"]["QuoteData"][0]["All"]["lastTrade"])


# ------------------------------------------------------------------- the rails

class Refused(Exception):
    """A rail said no. Never caught and downgraded to a warning — that is the
    entire point of having them."""


def check_rails(leg: dict, held: dict, rest: list[dict], last: float,
                allow_buy: bool, force_day: bool) -> list[str]:
    sym, act, qty = leg["symbol"], leg["action"], leg["qty"]
    notes: list[str] = []

    if leg["priceType"] == "MARKET":                                        # R10
        raise Refused("R10 MARKET refused. This script transmits pre-written "
                      "EXITS only; an entry is a live decision and belongs to a "
                      "human looking at a quote.")

    if act not in SELL_ACTIONS and not allow_buy:                            # R2
        raise Refused(f"R2 {act} refused without --i-am-buying. On a margin "
                      "account a mis-sized buy is the most expensive mistake "
                      "available; sells only reduce risk.")

    if act in SELL_ACTIONS:
        have = held.get(sym, {}).get("qty", 0)
        if have <= 0:
            raise Refused(f"R3 no {sym} position held — cannot sell it.")
        if qty > have:                                                       # R3
            raise Refused(f"R3 sell {qty:,.0f} > {have:,.0f} held in {sym}.")
        notes.append(f"R3 ok — {qty:,.0f} of {have:,.0f} held")

        mine_is_stop = "STOP" in (leg["priceType"] or "")
        same_side = sum(
            r["qty"] for r in rest
            if r["symbol"] == sym and r["action"] in SELL_ACTIONS
            and ("STOP" in (r["priceType"] or "")) == mine_is_stop
        )
        if same_side + qty > have:                                           # R4
            raise Refused(
                f"R4 {sym}: {same_side:,.0f} sh already resting on the same side "
                f"+ {qty:,.0f} new = {same_side + qty:,.0f} > {have:,.0f} held.")
        if same_side:
            notes.append(f"R4 ok — {same_side:,.0f} sh resting same-side, still within {have:,.0f}")

        opposite = sum(
            r["qty"] for r in rest
            if r["symbol"] == sym and r["action"] in SELL_ACTIONS
            and ("STOP" in (r["priceType"] or "")) != mine_is_stop
        )
        if opposite:
            notes.append(f"R4 note — {opposite:,.0f} sh resting on the OTHER side "
                         "(the no-OCO overlap). Run `reconcile` after any fill.")

        px = float(leg.get("stopPrice") if mine_is_stop else leg.get("limitPrice"))
        if mine_is_stop and px >= last:                                      # R5
            raise Refused(f"R5 sell STOP ${px:,.2f} is AT/ABOVE last ${last:,.2f} — "
                          "it would trigger instantly as a market order.")
        if not mine_is_stop and px <= last:
            raise Refused(f"R5 sell LIMIT ${px:,.2f} is AT/BELOW last ${last:,.2f} — "
                          "it would fill instantly, not on strength.")
        notes.append(f"R5 ok — {'stop' if mine_is_stop else 'limit'} ${px:,.2f} vs last ${last:,.2f}")

        away = abs(px / last - 1) * 100                                      # R6
        if away > SANITY_PCT:
            raise Refused(f"R6 ${px:,.2f} is {away:.1f}% from last ${last:,.2f} "
                          f"(band {SANITY_PCT}%). Looks like a decimal slip.")
        notes.append(f"R6 ok — {away:.1f}% from last")

    if leg["orderTerm"] == "GOOD_FOR_DAY" and "STOP" in (leg["priceType"] or "") \
            and not force_day:                                               # R7
        raise Refused("R7 GOOD_FOR_DAY on a STOP refused without --force-day. "
                      "A day order expires at the close and tomorrow the level "
                      "is unwatched — that is the 2026-08-10 failure.")
    notes.append(f"R7 ok — {leg['orderTerm']}")
    return notes


# ------------------------------------------------------------- preview / place

def _body(leg: dict, client_id: str) -> dict:
    order = {
        "allOrNone": "false",
        "priceType": leg["priceType"],
        "orderTerm": leg["orderTerm"],
        "marketSession": "REGULAR",
        "Instrument": [{
            "Product": {"securityType": "EQ", "symbol": leg["symbol"]},
            "orderAction": leg["action"],
            "quantityType": "QUANTITY",
            "quantity": str(int(leg["qty"])),
        }],
    }
    if leg.get("limitPrice") is not None:
        order["limitPrice"] = f"{float(leg['limitPrice']):.2f}"
    if leg.get("stopPrice") is not None:
        order["stopPrice"] = f"{float(leg['stopPrice']):.2f}"
    return {"orderType": "EQ", "clientOrderId": client_id, "Order": [order]}


def _client_id() -> str:
    # E*TRADE caps clientOrderId at 20 alphanumeric characters; anything longer
    # or punctuated fails with an error that reads like an auth problem.
    return datetime.now().strftime("ord%y%m%d%H%M%S%f")[:20]


def transmit(acct: dict, leg: dict, do_place: bool) -> dict:
    cid = _client_id()
    body = _body(leg, cid)
    key = acct["accountIdKey"]

    prev = etrade.post(f"/v1/accounts/{key}/orders/preview",
                       {"PreviewOrderRequest": body})                        # R1
    pr = prev.get("PreviewOrderResponse", {})
    pids = [p["previewId"] for p in (pr.get("PreviewIds") or [])]
    msgs = [m.get("description") for m in (pr.get("Messages", {}).get("Message") or [])]
    out = {"previewId": pids, "messages": msgs, "placed": False}
    if not pids:
        raise Refused(f"R1 preview returned no previewId — refusing to place. {prev}")
    if not do_place:
        return out

    place = etrade.post(f"/v1/accounts/{key}/orders/place", {"PlaceOrderRequest": {
        **body, "PreviewIds": [{"previewId": p} for p in pids]}})
    po = place.get("PlaceOrderResponse", {})
    out["placed"] = True
    out["orderId"] = [o.get("orderId") for o in (po.get("OrderIds") or [])]
    LOG.parent.mkdir(exist_ok=True)                                          # R9
    with LOG.open("a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(), "leg": leg,
                            "body": body, "response": po}) + "\n")
    return out


# --------------------------------------------------------- the EXIT PLAN block

ROW = re.compile(
    r"^\|\s*(?P<label>[^|]+?)\s*\|\s*(?P<action>BUY|SELL)\s*\|\s*(?P<qty>[\d,]+)\s*\|"
    r"\s*(?P<type>STOP|LIMIT|STOP_LIMIT)\s*\|\s*\$?(?P<price>[\d,.]+)\s*\|"
    r"\s*(?P<term>GTC|DAY)\s*\|", re.I | re.M)


def journal_legs(path: Path) -> list[dict]:
    """Parse every `## ⚑ EXIT PLAN — TICKER` block into placeable legs.

    **A table, not prose, and the reason is the whole design.** Everything else in
    the journal is written for a human to reason with. This one block is written
    for a script to execute, and those are different jobs. A regex over prose
    either guesses or misses, and a miss here is silent — the file reads exactly
    the same whether the order is in the market or not.

    So: **an EXIT PLAN heading whose table yields no legs is an ERROR, never an
    empty list.** Returning `[]` would report "nothing to place" for a position
    whose exit is written down and unplaced, which is precisely the failure this
    repo exists to catch.
    """
    if not path.exists():
        raise Refused(f"{path.name} not found. Copy JOURNAL.template.md to "
                      "JOURNAL.md and write your own gates — this script will "
                      "not invent a level.")
    txt = path.read_text(encoding="utf-8")
    blocks = re.split(r"^##\s*⚑?\s*EXIT PLAN\s*[—\-–]\s*", txt, flags=re.M)[1:]
    if not blocks:
        raise Refused("no `## ⚑ EXIT PLAN — TICKER` block found. See "
                      "JOURNAL.template.md for the shape.")

    legs: list[dict] = []
    for b in blocks:
        sym = b.split("\n", 1)[0].strip().split()[0].upper()
        body = b.split("\n## ", 1)[0]
        found = list(ROW.finditer(body))
        if not found:
            raise Refused(f"EXIT PLAN for {sym} has no readable rows. REFUSING — "
                          "an unreadable plan must not report as nothing to do.")
        for m in found:
            typ = m.group("type").upper()
            px = float(m.group("price").replace(",", ""))
            legs.append({
                "symbol": sym,
                "action": m.group("action").upper(),
                "qty": int(m.group("qty").replace(",", "")),
                "priceType": typ,
                "stopPrice": px if "STOP" in typ else None,
                "limitPrice": px if "LIMIT" in typ else None,
                "orderTerm": TERMS[m.group("term").upper()],
                "label": f"{sym} {m.group('label').strip()} — {typ} ${px:,.2f}",
            })
    # Stops first, always: the only leg that caps a loss. If placement is
    # interrupted halfway, hold the half that protects you.
    legs.sort(key=lambda l: 0 if "STOP" in l["priceType"] else 1)
    return legs


# ------------------------------------------------------------------------- cli

def _run(acct, legs, args):
    held, rest = positions(acct), resting(acct)
    ok = True
    for leg in legs:
        last = last_price(leg["symbol"])
        print(f"\n── {leg.get('label') or leg['symbol']}")
        print(f"   {leg['action']} {leg['qty']:,.0f} {leg['symbol']} {leg['priceType']} "
              f"{'stop $%.2f' % leg['stopPrice'] if leg.get('stopPrice') else ''}"
              f"{'limit $%.2f' % leg['limitPrice'] if leg.get('limitPrice') else ''} "
              f"{leg['orderTerm']}   (last ${last:,.2f})")
        try:
            for n in check_rails(leg, held, rest, last, args.i_am_buying, args.force_day):
                print(f"   ✅ {n}")
            r = transmit(acct, leg, args.place)
            for m in r["messages"] or []:
                print(f"   ℹ️  broker: {m}")
            if r["placed"]:
                print(f"   🟢 PLACED — orderId {r['orderId']}")
                rest.append({**leg, "status": "OPEN"})   # so R4 sees it next loop
            else:
                print(f"   ⚪️ DRY RUN — preview {r['previewId']} accepted. "
                      "Add --place to transmit.")
        except Refused as e:
            ok = False
            print(f"   🔴 REFUSED — {e}")
        except Exception as e:
            ok = False
            print(f"   🔴 ERROR — {e}")
    return 0 if ok else 1


def cmd_show(acct, args):
    held, rest = positions(acct), resting(acct)
    print("HELD")
    for s, d in held.items():
        print(f"  {s:6} {d['qty']:>10,.0f} sh @ ${d['last']:,.2f}")
    print(f"\nRESTING ({len(rest)})")
    for r in rest:
        print(f"  #{r['orderId']} {r['action']} {r['qty']:,.0f} {r['symbol']} "
              f"{r['priceType']} lim={r['limitPrice']} stop={r['stopPrice']} "
              f"{r['orderTerm']} [{r['status']}]")
    if not rest:
        print("  — none. Any price gate in your journal is on paper only.")
    return 0


def cmd_reconcile(acct, args):
    """After a partial exit the no-OCO structure leaves stale legs. Name them."""
    held, rest = positions(acct), resting(acct)
    bad = []
    for sym in {r["symbol"] for r in rest}:
        have = held.get(sym, {}).get("qty", 0)
        for side in (True, False):
            legs = [r for r in rest if r["symbol"] == sym
                    and r["action"] in SELL_ACTIONS
                    and ("STOP" in (r["priceType"] or "")) == side]
            tot = sum(r["qty"] for r in legs)
            if tot > have:
                bad.append((sym, "stop" if side else "limit", tot, have, legs))
    if not bad:
        print("✅ no over-committed side. Nothing to reconcile.")
        return 0
    for sym, side, tot, have, legs in bad:
        print(f"🔴 {sym}: {tot:,.0f} sh of resting {side} vs {have:,.0f} held.")
        for r in legs:
            print(f"     cancel or resize #{r['orderId']} ({r['qty']:,.0f} sh)")
        print(f"     -> `python place_order.py cancel <id>` then re-place at {have:,.0f}")
    return 1


def cmd_cancel(acct, args):
    r = etrade.put(f"/v1/accounts/{acct['accountIdKey']}/orders/cancel",
                   {"CancelOrderRequest": {"orderId": int(args.order_id)}})
    print(json.dumps(r, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--place", action="store_true", help="actually transmit (R8)")
    ap.add_argument("--i-am-buying", action="store_true", help="allow a BUY (R2)")
    ap.add_argument("--force-day", action="store_true", help="allow GOOD_FOR_DAY stop (R7)")
    ap.add_argument("--journal", default=None, help="path to your journal (default JOURNAL.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    sub.add_parser("from-journal")
    sub.add_parser("reconcile")
    c = sub.add_parser("cancel"); c.add_argument("order_id")
    o = sub.add_parser("order")
    o.add_argument("symbol"); o.add_argument("action"); o.add_argument("qty", type=int)
    o.add_argument("price_type", choices=["LIMIT", "STOP", "STOP_LIMIT", "MARKET"])
    o.add_argument("--limit", type=float); o.add_argument("--stop", type=float)
    o.add_argument("--term", default="GOOD_UNTIL_CANCEL")
    args = ap.parse_args()

    acct = _account()
    if args.cmd == "show":
        return cmd_show(acct, args)
    if args.cmd == "reconcile":
        return cmd_reconcile(acct, args)
    if args.cmd == "cancel":
        return cmd_cancel(acct, args)
    if args.cmd == "from-journal":
        legs = journal_legs(Path(args.journal) if args.journal else JOURNAL)
    else:
        legs = [{"symbol": args.symbol.upper(), "action": args.action.upper(),
                 "qty": args.qty, "priceType": args.price_type,
                 "limitPrice": args.limit, "stopPrice": args.stop,
                 "orderTerm": args.term, "label": None}]
    if args.place:
        print("⚠️  --place is set. These will be TRANSMITTED to E*TRADE.")
    return _run(acct, legs, args)


if __name__ == "__main__":
    sys.exit(main())
