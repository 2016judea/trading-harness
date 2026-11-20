# Data model: the Trading Harness connector

Written 2026-11-20, before the connector code, from the need alone (BUSINESS.md rule 5).
The need: a stranger in a Claude chat asks *"what is this business worth against its price?"*
or *"what did my trades actually do, and is the stop I wrote down resting?"* The connector
returns the numbers; their Claude does the reasoning. It never holds a brokerage login and
never places, previews or stages an order.

## Valuation side (public filings, no login)

**Company**: `ticker`, `name`, `cik`. *Which filer is this, exactly?*

**Line item, per fiscal year**: `line` (revenue, net income, CFO, capex, D&A, equity,
goodwill, debt…), `fiscal_year`, `value`, `xbrl_tag`, `form`, `accession`, `period_end`.
*What did the company itself report, and where can I check it?*

**Owner-earnings year**: `fiscal_year`, `net_income`, `depreciation`, `amortization`,
`capex`, `owner_earnings`, `fcf` (CFO − capex, the independent cross-check).
*How much cash could an owner take out without shrinking the business?*

**Valuation** (one company, one moment)
- inputs: `price`, `long_bond_pct` (+ `bond_date`, `bond_source`), `growth_pct`,
  `hurdle_pct`, `terminal_pct`, `shares_outstanding`
- outputs: `normalised_owner_earnings`, `return_on_tangible_capital`, `roe`,
  `market_cap`, `enterprise_value`, `owner_earnings_yield`, three `anchors`
  (bond-equivalent, two-stage DCF on normalised, same on latest) per share,
  `margin_of_safety_ladder` (0/25/33%), `reverse_dcf` (growth the price requires at
  8/9/10/12%), `sensitivity` grid
- `refusal` (null or the reason): <3 positive owner-earnings years in five, a loss in
  the latest year, or owner earnings >1.6× actual FCF. *Is there a durable earning power
  to value at all?* A refusal replaces the value, never sits beside it.
- `flags`: growth capex (capex >1.15× D&A), delivered CAGR >30%, short history (<5 FY).
- `calibration_note`: absolute values move with the hurdle; the relative outputs don't.

**Comparison row**: `ticker`, `price`, `implied_growth`, `delivered_growth`, `gap`,
`basis` (latest vs average, and why), `classification`. *Which of these is priced for
less than it already does?*

## Your-own-data side (pasted CSV, nothing stored)

**Trade**: `date` (exchange clock), `symbol`, `side`, `quantity`, `price`, `fee`, `id`.
**Closed lot**: `symbol`, `qty`, `buy_date`, `buy_price`, `sell_date`, `sell_price`,
`gain` (after fees), `return_pct`, `hold_days`. *What did each trade actually make?*
**Orphan sell**: a sell with no buy in the data. *Which results can't be known, and why?*
**Summary**: lots, net, win rate, avg win, avg loss, payoff, avg hold, orphans excluded.

**Position**: `symbol`, `quantity`, `last_price`, `market_value`, `unrealized_gain`,
`pct_of_portfolio`, `lot_dates`. *How big is each name, and what would selling it realize?*

**Resting order**: `order_id`, `symbol`, `side`, `price_type`, `quantity`, `filled`,
`limit_price`, `stop_price`, `term` (GTC/day), `status`, `linked_order_id`.
*What is actually at the broker tonight?*

**Journal gate**: `id`, `symbol`, `kind` (stop/limit/dated exit), `price`, `qty`, `date`.
*What did I write down that I would do?*
**Finding**: `severity` (critical/warning/ok/unchecked/dormant), `message`.
*Where do the written gate and the broker disagree?*

**Target**: `symbol`, `weight_pct`; plus `cash_on_hand`, `cash_reserve`, `prices`.
**Order-list row**: `symbol`, `current_$`, `current_%`, `target_%`, `delta_$`, `shares`,
`action`, `tax_on_exit`. *What would it take to get from here to there?*

## Reality check, field by field

| Field | Source | Fillable? |
|---|---|---|
| Company, line items, tags, accession | SEC XBRL companyfacts, keyless (needs a contact User-Agent) | yes |
| Owner earnings, FCF, ROTC | derived from the line items | yes |
| `long_bond_pct` | Treasury daily par yield curve CSV, keyless | yes, with its date |
| `price` | no free keyless quote API reachable from a server we can rely on | **from the chat**: the user or their model passes it |
| `shares_outstanding` | SEC `dei:EntityCommonStockSharesOutstanding` | yes (latest filing, not today's count) |
| Trades | the user's broker export, pasted | yes: E*TRADE's download, this repo's API CSV, or generic columns |
| Positions | the user's broker export, pasted | yes, except `lot_dates`: a positions export has no lots, so the tax tag can't split short- from long-term |
| Resting orders | no broker exports open orders as a CSV that we know of | **from the chat**: generic columns the model fills from the user's order screen |
| Journal gates | the user's journal text in this repo's template | yes for the template; anything else reads as unchecked, never as passing |

Two fields come from the chat rather than from a source (`price`, resting orders), and one
is missing outright (`lot_dates`). None of them is faked: the valuation without a price
still returns the owner-earnings history and the refusals, and the tax tag says it can't
see holding periods. That is the same scope the CLI already has, so the model is filled
and the build goes ahead on the full model, not a subset.
