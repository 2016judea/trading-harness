---
name: etrade-pull
description: >-
  Pull a live E*TRADE account — positions, weights, cost basis, unrealized P&L, or
  full transaction history — via this repo's OAuth 1.0a client. Use whenever real
  account numbers are needed rather than a reconstruction, or when an auth handshake
  fails, 401s, or hangs. Owns everything about E*TRADE authentication, including the
  token that dies at midnight Eastern and the interactive prompt that hangs in a
  non-interactive shell.
grounded: 2026-08-17 — the auth failures this wrapper was written around
---

# Pull live E*TRADE data

Scripts: `fetch_portfolio.py` → `data/portfolio.csv`; `fetch_transactions.py` →
`data/transactions.csv`; `etrade.py` is the client to import.

```bash
source .venv/bin/activate
ETRADE_ENV=prod python fetch_portfolio.py
ETRADE_ENV=prod python fetch_transactions.py --start 2023-01-01
```

Called from **[[portfolio-review]]**, which is the entry point for anything that
starts "what do they hold."

## Rules

- **`ETRADE_ENV=prod` on every single call.** Unset means sandbox, and the sandbox
  returns four canned accounts with **zero securities trades**, dated 2013. *It does
  not error and it does not look empty — it looks like a real account belonging to
  someone who never trades.* The OAuth URLs are hardcoded to prod either way, so a
  successful login proves nothing about which environment you're reading.

- **Never print or echo the contents of `.env` or `.tokens`.** They are the account.
  A traceback that includes the consumer secret has published it.

- **Re-pull rather than reuse a CSV from a previous session.** Prices move; weights
  computed off yesterday's file are wrong in a way nobody notices.

## The auth gotcha

OAuth 1.0a with an out-of-band verifier: there is no redirect, the user reads a
short code off E*TRADE's page and pastes it back.

**Tokens go inactive after ~2 hours idle and expire at midnight US Eastern, every
night.** `etrade.get()` catches the resulting 401 and re-runs the handshake once,
which is enough for a long-running script to cross the boundary.

**But the handshake needs a human, and `input()` hangs in a non-interactive shell** —
which is where an agent usually is. Split it across two invocations instead:

```bash
python auth_cli.py start            # prints the authorize URL — give it to the user
python auth_cli.py finish <code>    # exchange the code they paste back
```

If a pull hangs with no output, this is why. Kill it and use `auth_cli.py`.

## What the API will not give you

- **~3 years of transactions.** Older dates return error 2003. A complete archive
  needs statement CSV exports from the web UI.
- **A request with no date range silently returns only the last ~month.**
  `fetch_transactions.py` always sends an explicit window for this reason.
- Field shapes and the sandbox's exact limitations are documented in `SCHEMA.md`.
