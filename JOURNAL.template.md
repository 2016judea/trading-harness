# Trade Journal — template

One entry per position, written **before** conviction is tested. The whole point:
the hold/exit decision is pre-committed here, in writing, so it isn't made live in
a moment of fear or greed.

Copy this to `JOURNAL.md` (gitignored) and fill it in. Everything below is a
structure, not advice — the numbers are placeholders and the rules are examples of
the *kind* of rule worth pre-committing, not ones you should adopt.

---

## Portfolio rules (apply to every position)

Write these once, then stop renegotiating them. Each rule carries its reason,
because a rule without its reason gets rationalized around the first time it's
inconvenient.

- **Firewall:** which money this journal governs, and which money it never touches.
  *(Without this, a bad week in the active sleeve starts making claims on the
  retirement account.)*
- **Position sizing:** max % per name; max % for any group that shares a risk.
  *(If you don't use stop-losses, size is your only risk control — so it has to be
  decided before you're excited about something.)*
- **Correlated groups:** name the positions that die on the same news and cap them
  as one. *(Two names, one thesis, is one position wearing a disguise.)*
- **Drawdown rule:** hold through price drawdowns while the written invalidation is
  still false. *(Price falling is not new information. Your invalidation is.)*
- **Exit triggers**, exhaustively: (1) invalidation turns true — any price, any
  time; (2) the sell-into-strength signal fires; (3) a time limit passes with the
  thesis unconfirmed. *(Three named ways out means every exit is one of them, not
  a mood.)*
- **Entry method:** how you scale in, and the rule that adding later is a *new*
  decision that must re-clear the thesis. *(Averaging down without re-underwriting
  is how a small mistake becomes the largest line in the book.)*
- **What is never a reason to sell:** e.g. "it's red and the thesis is intact."

---

## ⚑ GATE REGISTER — every live trigger in one place

Every gate below is already written, in prose, in some position block. This table
is the index, not the authority — **the position block always wins on reasoning.**
It exists because once the file is long, reconstructing every live trigger from
prose is exactly where one gets missed.

**Fire on the TRANSITION, not the state.** A gate reading "under $227" is true
every day the price sits under $227. Acting on the *state* means re-deciding a
settled trade every morning; ignoring the noise means missing the crossing. So each
gate carries a state: `ARMED` (not yet crossed) → stamp `FIRED <date>` when it
crosses → it stays fired until you deliberately re-arm it, with a reason.

| Date | Name | What | Action it unlocks | State |
|---|---|---|---|---|
| YYYY-MM-DD | TICKER | earnings / lock-up expiry / ruling / ex-div | what this permits, with size | ARMED |
| YYYY-MM-DD | TICKER | lot turns long-term | trimming becomes tax-efficient | ARMED |
| YYYY-MM-DD | TICKER | time limit on an unconfirmed thesis | exit trigger 3 | not yet near |

**A. DATED** — above. Calendar, no price condition. No broker order expresses these.

**B. PORTFOLIO-STATE** — caps, pair limits, cash earmarks. *No broker order can
express these either*, which is the whole reason they live in a file.

**C. PRICE — these CAN be resting broker orders.** Split out precisely because
they can, and therefore because there are two different states: written down, and
actually in the market. `gate_check.py` reads this table and tells you which.

| # | Name | Trigger | Last | Distance | State |
|---|---|---|---|---|---|
| C1 | TICKER | ≥ $000.00 (what re-rate this would be) | $000.00 | +0.0% | ARMED |
| C2 | TICKER | ≥ $000.00 | $000.00 | +0.0% | ARMED |

> **A gate with no number is a hole, not a covered exit.** If a row's trigger
> reads "when the multiple expands well beyond growth," it will be decided live —
> the exact condition this file exists to prevent. Leave it visible and marked
> rather than inventing a number in the same breath as noticing the gap: picking
> one *there* is a live decision wearing a pre-commitment's clothes. `gate_check.py`
> reports these rows as `⬛ unchecked` and never as passing.

**⚑ EXIT PLAN — the machine-readable half of section C.** A price gate in the C
table is prose for a human. The block below is the same decision in a form
`place_order.py` can transmit without interpreting anything. Write one per
position that has a committed exit; delete it when the position closes.

## ⚑ EXIT PLAN — TICKER

| Leg | Action | Qty | Type | Price | Term |
|---|---|---|---|---|---|
| STOP | SELL | 1000 | STOP | 75.00 | GTC |
| TP1 | SELL | 500 | LIMIT | 88.00 | GTC |
| TP2 | SELL | 500 | LIMIT | 90.50 | GTC |

> **Why a table and not a sentence.** Everything else in this file is written for
> a human to reason with, and should stay that way. This one block is written for
> a script to *execute*, and those are different jobs. A parser over prose either
> guesses or misses, and a miss here is silent: the file reads exactly the same
> whether the order is in the market or not.
>
> **Order matters — the stop goes first.** It is the only leg that caps a loss;
> the take-profits only forgo an upside. If placement is interrupted halfway you
> want to be holding the half that protects you.
>
> **This will look over-committed, and that is deliberate.** 1,000 shares of stop
> plus 1,000 shares of limit against 1,000 held is fine *only* because the two
> sides straddle the current price and cannot both print. **Brokers whose API has
> no OCO — E\*TRADE's does not — leave you no other way to express a bracket.**
> The cost is that a fill on any leg leaves the others stale and oversized, so
> `place_order.py reconcile` is a required step after any fill, not an optional
> one. *Write a workaround down with its cleanup attached, or the workaround
> becomes the next silent failure.*

**D. FUNDAMENTAL INVALIDATIONS** — exit at any price. Cannot be automated; they
need judgement, which is why no watchdog replaces reading the file.

---

## Positions

One block per name. Keep dead positions in the file, marked `EXITED <date>` with
what actually happened — the record of a thesis that didn't work is the most
useful thing in here later.

## TICKER — Company · thesis bucket · target % · [tag]
- **Opened:** YYYY-MM-DD · basis $ · conviction:
- **Thesis:** the real-world thing you think is true. Not "it's cheap" — the
  mechanism.
- **Why I'm early:** the recognition gap. What does the market not price yet, and
  why hasn't it? *(If you can't answer this, you're buying consensus and should
  expect consensus returns.)*
- **Invalidation (EXIT now):** specific, fundamental, and **non-price**. A named
  event or number that means the thesis was wrong. *(Non-price is the whole
  discipline: "down 20%" isn't invalidation, it's volatility. Writing this before
  you own it is the only time you'll write it honestly.)*
- **Sell-into-strength signal:** what "the market caught up" looks like — the price
  level, the upgrade cluster, the story going mainstream. *(Decide how a win ends
  while you can still think about it calmly.)*
- **Recognition catalyst / timeline:** the dated events that would close the gap.

---

## Review log

Dated entries. What changed, what you did about it, and — most usefully — what you
considered and *didn't* do, with the reason. When a position later goes wrong, this
is the only record of whether you were unlucky or wrong.

**YYYY-MM-DD** — what happened, what it means for the thesis, what fires or doesn't.
Explicitly: does this meet a written invalidation, or is it just bad news? *(Most
bad news is not invalidation. Making yourself answer in writing is the point.)*

---

## Template for a new position

```
## TICKER — Company · thesis bucket · target % · [tag]
- Opened: YYYY-MM-DD · basis $ · conviction:
- Thesis: (the real-world reality you saw)
- Why I'm early: (the recognition gap — what the market hasn't priced)
- Invalidation (EXIT now): (specific, fundamental, NON-price)
- Sell-into-strength signal: (what "the market caught up" looks like)
- Recognition catalyst / timeline:
```
