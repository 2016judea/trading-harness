---
name: realized-pnl
description: >-
  Work out what someone's trades actually did — FIFO-matched closed lots, realized
  gain by symbol, best and worst trades, holding periods — and run the counterfactual
  on every loss they took. Use when asked "how have I actually done", "what were my
  worst trades", "should I have held", "does cutting losers cost me money", or when
  a portfolio review needs behavior rather than holdings.
grounded: 2026-08-17 — reconstructing three years of trades from the API
---

# Reconstruct realized P&L

Needs `data/transactions.csv` first — see **[[etrade-pull]]**.

```bash
python realized_pnl.py        # closed lots, best/worst, by-symbol roll-up
python backtest_holding.py    # for every loss taken: what if it had been held?
```

`load.py` does the normalizing (epoch → datetime, trades split from cash) and is
importable: `from load import trades, cash`.

## Rules

- **The in-window total is not lifetime performance.** Sells whose matching buys
  predate the ~3-year API window come back in a separate `orphan_sells` table with
  no cost basis. *They are usually the oldest and largest winners, so quoting the
  net realized figure as "how they've done" can invert the sign of the answer.*
  Report the closed-lot total and the orphan count, always together.

- **FIFO is an assumption, not their tax treatment.** The matcher pairs each sell
  against the oldest open buy. If the broker used specific-lot identification, the
  per-lot gains here won't match the 1099. *Fine for reading behavior, wrong for
  filing.* Say which one you're doing.

- **Dividends carry a symbol but are not trades.** `load.py` keys off
  `transactionType`, not the presence of a ticker, for exactly this reason. If you
  filter the raw CSV yourself, don't reintroduce the bug.

## The counterfactual is usually the finding

`backtest_holding.py` answers one question: for every lot sold at a loss, what
would it be worth held to today, and at its 52-week high?

This is the highest-value output in the repo for most people, because **a book can
look healthy on holdings and still be bleeding from a repeated exit mistake.** That
pattern is invisible in a positions table.

Read it carefully:

- It ignores redeployment, dividends and taxes. It isolates one question — did
  cutting on price cost or save money — and answers only that. *The capital didn't
  sit in cash in real life, so this is not a portfolio-level P&L claim.*
- It needs live quotes, so it hits the API. Auth applies: **[[etrade-pull]]**.
- A large, consistent counterfactual against the person is a **behavioral** finding.
  Surface it to **[[portfolio-review]]** as such, and pair it with the temperament
  section of **[[buffett-checklist]]** rather than a list of the individual trades.
- One or two bad exits is not a pattern. Look for direction across most of the
  losing names.
