# trading-harness

Pull your own E*TRADE account over the API, work out what actually happened, and
decide the next trade *before* you're in it.

Three parts, and you can take any one of them:

- **The client** — a working [E*TRADE REST API](https://developer.etrade.com/home)
  wrapper in ~90 lines, plus analysis on top: FIFO realized P&L, position dumps,
  target-allocation order lists, price history and seasonality.
- **The reconciler** — [`gate_check.py`](gate_check.py), which answers the one
  question a journal cannot: *is the gate I wrote down actually resting at the
  broker?* Twice it wasn't, and both times a human found it by hand.
- **The valuation** — [`sec_financials.py`](sec_financials.py) pulls a company's
  as-reported figures from the SEC's free XBRL API, and
  [`owner_earnings.py`](owner_earnings.py) / [`compare.py`](compare.py) turn them
  into owner earnings, a reverse DCF, and a ranked screen. No API key, and no
  number typed by hand.
- **The skills** — [`.claude/skills/`](.claude/skills/), eight of them, so an agent
  can run the whole review without being told how. One entry point that
  forward-references a skill per tool.
- **The journal** — [`JOURNAL.template.md`](JOURNAL.template.md), a written format
  for pre-committing why you own a thing and what would make you sell it, so the
  hold/exit call isn't made live in a bad moment.

**Nothing here can place a trade.** Every script reads, computes and prints. The
order lists are lists — you type the orders yourself.

## Valuation, without credentials or a key

The SEC half needs no key — only a contact address, because SEC blocks clients
that don't identify one:

```bash
export SEC_USER_AGENT="Your Name you@example.com"
python3 sec_financials.py CTVA --years 7          # as-reported 10-K lines
python3 owner_earnings.py CTVA --price 82.81 --bond 5.19
python3 compare.py --bond 5.19 RMD=232.45 KO=91.99 CTVA=82.81
```

`owner_earnings.py` prints owner earnings by year, return on tangible capital,
what the market is asking in owner-earnings terms, a margin-of-safety ladder, and
a **reverse DCF** — the growth today's price requires, which is the only part of
a valuation nobody can argue with. `compare.py` ranks a set on the gap between
the growth a price requires and the growth the business has actually delivered.

Two things it does on purpose. It **refuses**: fewer than three positive
owner-earnings years in five, a loss in the latest year, or owner earnings more
than 1.6x actual free cash flow (which means the D&A add-back is carrying a
goodwill impairment, not depreciation) all return "too hard" instead of a number.
And it **calibrates** — run it on a business Buffett actually owns before
believing any absolute verdict, because a 10% hurdle against a 5%+ long bond
calls almost everything overvalued. Un-calibrated, it rates Coca-Cola more
overvalued than most of what you'd screen against it. The durable outputs are the
relative ones.

## Try it without credentials

The repo ships example data, so everything but the live pulls runs on a fresh clone:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp data/example_transactions.csv data/transactions.csv
python realized_pnl.py                       # FIFO-matched closed lots + win rate
python rebalance.py --portfolio data/example_portfolio.csv --targets targets.example.json
python price_history.py AAPL                 # real data, no key needed

# the reconciler, on example data: an over-cap position with no stop, a
# good-for-day order that dies tonight, and two loose legs over-committing a lot
python gate_check.py --portfolio data/example_portfolio.csv \
                     --orders data/example_orders.csv \
                     --journal JOURNAL.template.md --allow-stale
```

`place_order.py` needs a live account (a preview is a real API call), but its
journal parser doesn't:

```bash
python -c "from pathlib import Path; import place_order as p; \
           print(p.journal_legs(Path('JOURNAL.template.md')))"
```

Note the stop sorts first regardless of the order you wrote the rows in. If
placement is interrupted halfway, you want to be holding the half that protects
you.

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
python fetch_orders.py          # -> data/orders.csv + data/orders_raw.json
python gate_check.py            # journal gates vs what is actually resting
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

### The orders endpoint has its own traps

- **A 204 No Content is the normal answer when nothing matches.** It returns an
  empty body rather than an empty list, so a naive `.json()` raises on a perfectly
  good response. `etrade.get()` returns `{}` instead.
- **No date range returns a short window, silently** — same shape as transactions.
- **The default is not `status=OPEN`,** and you do not want it to be: an `EXPIRED`
  row is the diagnostic for a good-for-day order that died overnight, and filtering
  to open orders hides exactly the failure you are looking for.
- **One order carries a list of details, each carrying a list of instruments.** A
  bracket or multi-leg order is several rows sharing an `orderId`; the sibling
  pointers (`replacesOrderId`, `replacedByOrderId`) are what distinguish a linked
  OCO pair from two loose orders that over-commit the same shares.
- **The field mapping is not yet validated against a live account** — the sandbox
  has zero orders. `fetch_orders.py` therefore writes the raw payload to
  `data/orders_raw.json` every run. Read it once, on the first real pull, before
  trusting a green report: a mis-mapped `stopPrice` reports a protected position
  that isn't.

### ⚠️ There is no OCO. Find out here, not in your plan.

`priceType` covers MARKET / LIMIT / STOP / STOP_LIMIT / trailing / hidden;
`orderTerm` covers GOOD_UNTIL_CANCEL / GOOD_FOR_DAY / GOOD_TILL_DATE /
IMMEDIATE_OR_CANCEL / FILL_OR_KILL; and the only linkage fields are
`conditionType` (`CONTINGENT_GTE` / `CONTINGENT_LTE`) against **another symbol's**
price. **Nothing links two orders on the same symbol so that one cancels the
other.** OCO is a Power-E*TRADE UI construct, not an API capability.

So a bracket written as *"976 shares, limit $88 / stop $75, one-cancels-other"* is
not expressible here at all. The sequential form is — stop on everything first,
then the take-profit tranches — and it is **over-committed by construction**,
which is safe only because the two sides straddle the last price and cannot both
print. The cost is that any fill leaves a stale, oversized leg on the other side.
`place_order.py reconcile` finds those, and running it after a fill is a required
step rather than an optional one.

### A preview creates no order, and that makes it the test you want

`POST /orders/preview` validates the whole payload against the live account —
symbol, price sanity, buying power, position — and returns the `previewId` that
`place` requires. It costs nothing and changes nothing. **Use it as your
integration test; do not use the sandbox for orders,** which holds no positions
and therefore exercises nothing about a sell.

## What each script does

| Script | |
|---|---|
| `etrade.py` | OAuth 1.0a session + thin REST helpers. Import this. |
| `auth_cli.py` | Two-step handshake for non-interactive environments. |
| `fetch_portfolio.py` | Current positions, cost basis, unrealized P&L → CSV. |
| `fetch_transactions.py` | Full transaction history, paginated → CSV. |
| `load.py` | Normalizes the raw CSV; splits trades from cash movements. |
| `fetch_orders.py` | Every order in a window, flattened to one row per leg → CSV. |
| `gate_check.py` | Journal gates vs resting orders. Non-zero exit when something is missing. |
| `place_order.py` | Transmits the exits your journal already committed to. Preview-first, dry-run by default, ten rails. |
| `realized_pnl.py` | FIFO-matches sells against buys. Closed lots, win rate, best/worst, by symbol. |
| `rebalance.py` | Target weights → order list, with a tax tag per row. Optional DCA schedule. |
| `price_history.py` | ~10y daily bars + monthly seasonality. No API key. |
| `backtest_holding.py` | For every loss you took: what if you'd held instead? |

`realized_pnl.py` reports sells with no matching buy inside the window separately
— their cost basis predates your data, and quietly treating them as zero-basis
would overstate your gains.

`price_history.py` reads Nasdaq's public quote endpoint, which needs only a
browser User-Agent. It's there because Yahoo's chart API started hard-429ing in
2026 and Stooq put a JavaScript proof-of-work wall in front of theirs.

## Placing an order: the narrow scope, on purpose

`place_order.py` transmits exits **that are already written down**. It decides
nothing, has no opinion about price, and cannot open a position.

```bash
python place_order.py show           # held vs resting
python place_order.py from-journal   # dry-run every ⚑ EXIT PLAN block
python place_order.py from-journal --place
python place_order.py reconcile      # stale legs after a fill
```

The scope is narrow for a reason worth stating plainly. In the book this tooling
came out of, **every failure was a transmission failure, not a decision failure**
— a good-for-day stop that expired overnight, a concentrated entry that went in
with nothing resting behind it, and the same stop still unplaced six days later.
The levels were correctly reasoned and written down in advance every time. So
automating the *decision* would have fixed none of it, while removing the one
judgement worth keeping. Automating *transmission* fixes all three.

The rails, each traceable to something that actually goes wrong:

| | |
|---|---|
| **R1** | preview always; the `previewId` is echoed into the place body |
| **R2** | SELL-only unless `--i-am-buying` — on margin, `marginBuyingPower` reads ~2x your real cash |
| **R3** | never sell more than is held, checked live rather than from a cached CSV |
| **R4** | resting + new may not over-commit *per side*; the stop/limit straddle is the allowed exception |
| **R5** | a sell STOP must be below last, a sell LIMIT above — inverted, either becomes an instant market order |
| **R6** | refuse anything >35% from last, because R5 alone accepts a $7.50 stop meant as $75.00 |
| **R7** | GTC by default; a GOOD_FOR_DAY stop is refused without `--force-day` |
| **R8** | dry run by default |
| **R9** | every transmitted order is logged to `data/placed_orders.log` |
| **R10** | MARKET refused outright — an entry is a decision and belongs to a human looking at a quote |

### Don't build a scheduler for this

The reflex after finding an unplaced stop is a nightly cron. It cannot work here:
**the access token expires every midnight ET and re-auth needs a browser**, so an
unattended check 401s every morning — and a watchdog that reports "cannot check"
100% of the time is worse than none, because its silence gets read as all-clear.

**A GTC order resting at the broker needs no token, no cron and no watchdog. It is
already the durable thing.** The monitoring gap exists only while the order is
unplaced; placing it correctly closes the gap with no infrastructure at all.
`gate_check.py` stays what it is — the audit you run when you sit down.

## The skills

[`.claude/skills/`](.claude/skills/) — written for [Claude
Code](https://claude.com/claude-code), but they're plain markdown and read fine in
any agent. **Start at `portfolio-review`**; it decides which pulls a given question
needs and hands off to the skill that owns each one.

```
portfolio-review        ← entry point: "review my book", "should I sell X"
├── etrade-pull         positions + transactions; owns OAuth and its failures
├── order-check         is the gate you wrote down actually resting at the broker?
├── realized-pnl        what the trades actually did, and the counterfactual
├── price-history       where a name sits in its own cycle
├── buffett-checklist   a skeptical second read on a single name
├── rebalance-plan      a decision, turned into an order list
└── trade-journal       write the exit down before it's tested
```

Each tool skill owns its own quirks so nothing rediscovers them: the token that
dies at midnight Eastern lives in `etrade-pull`, the ~3-year retention that makes a
realized total *not* lifetime performance lives in `realized-pnl`, the fact that
seasonality on ten years is n=10 per month lives in `price-history`.

Every rule in them carries its reason, which is deliberate — **a rule without its
reason gets rationalized around the first time it's inconvenient**, and these govern
money. The load-bearing one, in `portfolio-review`:

> **Never place an order, and never imply one was placed.** Nothing in this repo
> can. The analysis decides and shows its grounds; the person executes. An agent
> that both decides and executes removes the one checkpoint where a human can catch
> a wrong premise.

## The journal

The code tells you what happened. [`JOURNAL.template.md`](JOURNAL.template.md) is
for deciding what happens next, and it's the half I'd actually defend.

One block per position, written **before** conviction gets tested, naming the
thesis, the non-price condition that means you were wrong, and what "the market
caught up" would look like. Plus a gate register: every dated trigger in one
table, each carrying a state, because a rule like "buy under $227" is true every
day the price sits there — you want to act on the **crossing**, not the condition,
or you re-decide a settled trade every morning.

And a register still only records a *decision*. Whether the order exists is a fact
about the broker, and the two drift apart silently: a good-for-day limit expires
overnight, a stop gets written down and never placed. That gap is what
`gate_check.py` closes, and it is the reason the price gates live in their own
section of the register — they are the only ones a broker order can express, so
they are the only ones that can be in two states at once.

## Why the journal is a text file and not a database

The format comes out of a working method written up in
[*Getting an AI to do a given thing is really pretty simple*](https://aidanjude.substack.com/p/getting-an-ai-to-do-a-given-thing):
give an agent a concise set of instructions with clear goals and explicit rules;
write the rules down somewhere they can be *checked* rather than remembered; and
harvest them from work you actually did, rather than guessing at them up front.

A journal in that shape is readable by a person and by a model, which is the
point. The rules are explicit because well-defined bounds make a reasoning agent
more deterministic, and each one carries its reason because a rule without its
reason gets rationalized around the first time it's inconvenient. The gate
register exists so that an agent reading the file acts on a *crossing* instead of
re-litigating a settled trade every time it's asked.

The same method explains the shape of `.claude/skills/`. Every tool an agent has to
reach for gets its own skill definition, and skills cross-reference each other like
a paper — so when a task crosses outside one skill's scope it points at the skill
that owns that ground instead of restating it, and an assumption can be checked
rather than inherited blind. That's why the OAuth failure modes are documented once,
in `etrade-pull`, and everything else just links to it.

It also implies a division of labor, which this repo takes literally: the
analysis decides and shows its grounds, and **you** place the order.

## Notes

Python 3.9+. Dependencies: `requests`, `requests-oauthlib`, `python-dotenv`, `pandas`.

`SEC_USER_AGENT` must be set for anything touching SEC data; responses cache for
a day under `~/.cache/sec-financials`.

`.env`, `.tokens`, `targets.json` and everything in `data/` except the examples
are gitignored — your keys and your positions shouldn't end up in a commit.

This is a personal tool published because the E*TRADE half was annoying enough to
solve once. It is not investment advice, and the journal format is a description
of one person's discipline, not a recommendation. MIT licensed — do what you like
with it.
