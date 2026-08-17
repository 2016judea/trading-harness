# trading-harness

Pull your own E*TRADE account over the API, work out what actually happened, and
decide the next trade *before* you're in it.

Two halves that don't need each other:

- **The client** — a working [E*TRADE REST API](https://developer.etrade.com/home)
  wrapper in ~90 lines, plus analysis on top: FIFO realized P&L, position dumps,
  target-allocation order lists, price history and seasonality.
- **The harness** — [`JOURNAL.template.md`](JOURNAL.template.md), a written format
  for pre-committing why you own a thing and what would make you sell it, so the
  hold/exit call isn't made live in a bad moment.

**Nothing here can place a trade.** Every script reads, computes and prints. The
order lists are lists — you type the orders yourself.

## Try it without credentials

The repo ships example data, so everything but the live pulls runs on a fresh clone:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp data/example_transactions.csv data/transactions.csv
python realized_pnl.py                       # FIFO-matched closed lots
python rebalance.py --portfolio data/example_portfolio.csv --targets targets.example.json
python price_history.py AAPL                 # real data, no key needed
```

## Connect a real account

```bash
cp .env.example .env        # add your consumer key + secret
```

Get keys from [E*TRADE's developer portal](https://developer.etrade.com/home).
Sandbox keys are issued immediately; production keys require signing their API
agreement. Default is `sandbox` — set `ETRADE_ENV=prod` for your real account.

```bash
python -c "import etrade, json; print(json.dumps(etrade.list_accounts(), indent=2))"
python fetch_portfolio.py       # -> data/portfolio.csv
python fetch_transactions.py    # -> data/transactions.csv
```

The first call opens E*TRADE's authorize page in a browser and asks you to paste
back a short verifier code.

### Auth is OAuth 1.0a, and it's the fiddly part

- The callback must be the literal string `"oob"` (out-of-band). There is no
  redirect flow; the user copies a code off the page.
- Access tokens are cached in `.tokens` (gitignored) and reused.
- Tokens **go inactive after ~2 hours idle and expire at midnight US Eastern**,
  every night. `etrade.get()` catches the resulting 401 and re-runs the handshake
  once, so a long-lived script survives the boundary.
- The handshake needs a human, which breaks in a non-interactive shell. Use
  `auth_cli.py start` / `auth_cli.py finish <code>` to split it across two
  invocations (useful when an agent or cron job drives it).

### API limits worth knowing before you build on it

- **~3 years of transaction retention.** Older dates return error 2003. For a
  longer archive, export statement CSVs from the web UI.
- **A request with no date range returns only the last ~month**, silently.
  `fetch_transactions.py` therefore always sends an explicit window.
- The **sandbox has zero securities trades** — 4 canned accounts, 11 identical
  cash transactions each, dated 2013. You cannot validate any trade-analysis path
  against it. See [SCHEMA.md](SCHEMA.md).

## What each script does

| Script | |
|---|---|
| `etrade.py` | OAuth 1.0a session + thin REST helpers. Import this. |
| `auth_cli.py` | Two-step handshake for non-interactive environments. |
| `fetch_portfolio.py` | Current positions, cost basis, unrealized P&L → CSV. |
| `fetch_transactions.py` | Full transaction history, paginated → CSV. |
| `load.py` | Normalizes the raw CSV; splits trades from cash movements. |
| `realized_pnl.py` | FIFO-matches sells against buys. Closed lots, best/worst, by symbol. |
| `rebalance.py` | Target weights → order list, with a tax tag per row. Optional DCA schedule. |
| `price_history.py` | ~10y daily bars + monthly seasonality. No API key. |
| `backtest_holding.py` | For every loss you took: what if you'd held instead? |

`realized_pnl.py` reports sells with no matching buy inside the window separately
— their cost basis predates your data, and quietly treating them as zero-basis
would overstate your gains.

`price_history.py` reads Nasdaq's public quote endpoint, which needs only a
browser User-Agent. It's there because Yahoo's chart API started hard-429ing in
2026 and Stooq put a JavaScript proof-of-work wall in front of theirs.

## The journal

The code tells you what happened. [`JOURNAL.template.md`](JOURNAL.template.md) is
for deciding what happens next, and it's the half I'd actually defend.

One block per position, written **before** conviction gets tested, naming the
thesis, the non-price condition that means you were wrong, and what "the market
caught up" would look like. Plus a gate register: every dated trigger in one
table, each carrying a state, because a rule like "buy under $227" is true every
day the price sits there — you want to act on the **crossing**, not the condition,
or you re-decide a settled trade every morning.

There's an [`.claude/skills/buffett-checklist/`](.claude/skills/buffett-checklist/)
skill here too — a value-investing discipline lens for
[Claude Code](https://claude.com/claude-code), for arguing with yourself before
you buy.

## Notes

Python 3.9+. Dependencies: `requests`, `requests-oauthlib`, `python-dotenv`, `pandas`.

`.env`, `.tokens`, `targets.json` and everything in `data/` except the examples
are gitignored — your keys and your positions shouldn't end up in a commit.

This is a personal tool published because the E*TRADE half was annoying enough to
solve once. It is not investment advice, and the journal format is a description
of one person's discipline, not a recommendation. MIT licensed — do what you like
with it.
