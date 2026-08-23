---
name: order-check
description: >-
  Verify that the gates a journal says are live are actually resting at the broker.
  Use when someone says "did my order get placed", "is my stop in", "what orders do
  I have open", "check my open orders", "is that limit still live", after any entry
  into a position larger than its cap, after a consolidation or a rebalance, and as
  the first step of any review that is about to reason on top of a gate. Also use
  when a journal names a stop or limit and nothing has confirmed it exists.
grounded: >-
  2026-08-20 — a book went 100% into one name and the orders endpoint returned zero
  open orders; the stop existed only on paper. Ten days earlier a GOOD_FOR_DAY limit
  had expired overnight and the stock traded through the level unwatched. Both were
  found by hand, which is why this is a script.
---

# Check the orders that are supposed to exist

```bash
python fetch_orders.py            # -> data/orders.csv (+ orders_raw.json)
python gate_check.py              # reconcile journal gates vs resting orders
```

`gate_check.py` exits non-zero when something needs attention. Run it **before**
any analysis that assumes a gate is live, not after.

## The one thing this skill is for

**A written gate and a resting order are two different states, and a journal
cannot tell them apart.** Everything else here follows from that.

## Rules

- **Never report a gate as live because the journal says so.** The journal is a
  record of a decision, not of an order. *On 2026-08-20 the register said a stop
  was the position's only remaining risk control; the broker had nothing. Both
  statements were true at once.*

- **"No open orders" and "no orders file" must never render the same.**
  `gate_check.py` refuses to produce a verdict when `data/orders.csv` is missing,
  and says *zero resting orders* loudly when the file is real and empty. *A summary
  that collapses those two says "all clear" for the worst possible reason.*

- **A pull older than today is not evidence about today.** The script refuses to
  report on data older than 18 hours unless `--allow-stale` is passed, and then
  stamps the caveat on every run. *Orders are cancelled, filled and expired
  overnight; reconciling against yesterday's file is worse than not reconciling,
  because it reads as authoritative.*

- **GOOD_FOR_DAY on anything that is not a same-day view is a defect, not a
  preference.** Flagged critical. *The level was right on 2026-08-10; the duration
  created a gap where the fill existed and no order was there to catch it. Manual
  daily re-entry is not "more disciplined" — it just adds an execution gap.*

- **Past the per-name cap, a position with no resting stop is the finding.** Report
  it first, before weights, P&L or anything else. *A book that runs no stop-losses
  is relying on size as its risk control, and that is only true while no single name
  is big enough to hurt alone. Past the cap size controls nothing, and the stop is
  that same discipline expressed through the only variable left.*

- **Loose legs over-commit a position.** Two limits and a stop written against the
  same shares are not three gates; placed unlinked they are an oversized order
  waiting for the first fill. Report resting sell quantity against shares held, and
  say OCO/bracket when it exceeds. *This is arithmetic, and it is invisible in a
  per-order view.*

- **A gate on a name no longer held is DORMANT, not unplaced.** Nothing can rest
  against shares that don't exist. *Six of seven names left one book on a single day;
  without this distinction the report cried wolf on four rows out of nine, and a
  report that cries wolf is not read on the day it is right.*

- **An unchecked gate is not a passing gate.** Rows whose trigger carries no number
  ("when the multiple expands well beyond growth") print as `⬛ unchecked` and hold
  the exit code non-zero while the position is live. *A qualitative trigger is
  decided live, which is the exact condition a pre-commitment file exists to
  prevent. Leave the hole visible; do not invent the number here — picking one in
  the same breath as noticing the gap is a live decision wearing a
  pre-commitment's clothes.*

## What this cannot do

- **It cannot place, amend or cancel anything.** Nothing in this repo can, by
  design. It tells you what is missing; the person places it.
- **Portfolio-state gates are out of scope** — a cap on a *pair* of names, a cash
  earmark, a combined-exposure limit. No broker order expresses them, which is why
  they live in a file and why tier 1 only checks per-name stop coverage.
- **Fundamental invalidations are out of scope.** They need judgement. No watchdog
  replaces reading the position block.
- **Tier 2 is a best-effort parse of prose.** It labels every row it could not read
  and warns when a section it expected is absent. When that warning appears, tier 1
  is the only thing covering the book — say so rather than implying full coverage.

## The field mapping is not yet validated against a real account

`fetch_orders.py` is coded against E*TRADE's documented Orders shape, and the
sandbox has **zero orders** to check it with (see `SCHEMA.md`). The raw payload is
written to `data/orders_raw.json` on every run for exactly this reason. **On the
first live run, read the raw JSON and confirm the columns before trusting a green
report** — a mis-mapped `stopPrice` reports a protected position that isn't.

## See also

**[[etrade-pull]]** owns the OAuth handshake and its nightly expiry — the orders
endpoint dies at midnight Eastern with everything else. **[[portfolio-review]]**
should call this before it reasons about any gate. **[[trade-journal]]** is where a
newly-placed order gets written down, and where a gate's state is stamped.
