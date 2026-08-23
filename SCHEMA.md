# Transaction data — schema notes

What `/v1/accounts/{key}/transactions` actually returns, and where the sandbox
misleads you. Written down because two of these cost real debugging time.

## Columns

| Column | Notes |
|--------|-------|
| `transactionId` | unique id |
| `accountId` / `_accountId` | account number (`_accountId` is injected by `fetch_transactions.py`, so rows stay attributable after you concatenate accounts) |
| `transactionDate`, `postDate` | **epoch timestamps** — `load.py` auto-detects seconds vs milliseconds |
| `amount` | signed; negative = money out |
| `transactionType` | `Bought` / `Sold` / `Dividend` / `Interest` / `Transfer` / `Fee` / `Adjustment` … |
| `description`, `description2`, `memo` | free text |
| `brokerage.displaySymbol` | ticker — **empty in sandbox**, populated for real trades |
| `brokerage.quantity`, `.price`, `.fee` | trade economics; sells carry a negative quantity |
| `brokerage.settlementDate`, `.settlementCurrency`, `.paymentCurrency` | settlement |
| `imageFlag`, `storeId` | rarely useful |

## The sandbox does not contain trades

- 4 canned accounts, **11 identical transactions each** (Transfer / POS / Fee /
  Bill Payment).
- Dates span **2013-05-07 → 2013-06-03** only.
- **Zero securities trades.** `brokerage.displaySymbol` / `quantity` / `price` are
  all empty or zero.

So no trade-analysis path in this repo can be validated against the sandbox. The
example CSVs in `data/` exist for exactly that reason — they're invented, but
they're shaped like production and they exercise the FIFO matcher, including a
sell whose matching buy falls outside the window.

## API limits, learned the hard way

- **~3-year retention.** Any date older than three years returns error 2003
  ("Date should fall within the last three years"). A longer archive has to come
  from statement CSV exports in the web UI.
- **A request with no date range returns only the last ~month**, and tells you
  nothing about it. `fetch_transactions.py` always sends an explicit window for
  this reason — the failure mode is a quietly incomplete dataset, not an error.
- **Pagination is by `marker`**, not page number. Keep requesting until the
  response stops returning one.

## Classification (`load.py`)

`category` buckets each row: `trade` (Bought/Sold), `dividend`, `cash`,
`interest`, `adjustment`, `other`. `is_trade` is true only for actual buys and
sells — **dividends carry a symbol too**, so keying off the presence of a symbol
instead of the transaction type silently counts income as trading activity.


---

# Order data — schema notes

From `/v1/accounts/{key}/orders`, flattened by `fetch_orders.py` to **one row per
(order, detail, instrument)**. A bracket or multi-leg order is therefore several
rows sharing an `orderId`.

| Column | Notes |
|--------|-------|
| `orderId` | shared across the legs of one order |
| `status` | `OPEN` · `EXECUTED` · `CANCELLED` · `EXPIRED` · `REJECTED` · `PARTIAL` · `CANCEL_REQUESTED` |
| `orderTerm` | `GOOD_UNTIL_CANCEL` · `GOOD_FOR_DAY` · `GOOD_TILL_DATE` · `IMMEDIATE_OR_CANCEL` · `FILL_OR_KILL` |
| `priceType` | `MARKET` · `LIMIT` · `STOP` · `STOP_LIMIT` · trailing variants |
| `limitPrice`, `stopPrice`, `stopLimitPrice` | populated per `priceType`; blank otherwise |
| `orderAction` | `BUY` · `SELL` · `BUY_TO_COVER` · `SELL_SHORT` |
| `orderedQuantity`, `filledQuantity`, `averageExecutionPrice` | per instrument, not per order |
| `replacesOrderId`, `replacedByOrderId` | the sibling pointers. **Without these an OCO bracket is indistinguishable from two loose orders over-committing the same shares.** |
| `placedTime`, `executedTime` | epoch ms |

## Traps

- **204 No Content is the normal "nothing matched".** Empty body, not an empty
  list — `etrade.get()` returns `{}` rather than letting `.json()` raise.
- **No date range silently returns a short window.** `fetch_orders.py` always
  sends `fromDate`/`toDate` (default: last 90 days).
- **Do not filter to `status=OPEN`.** An `EXPIRED` row is the evidence that a
  good-for-day order died overnight, which is precisely the failure being hunted.
- **Sandbox has zero orders,** so none of the above is validated against live
  data yet — the same limitation as the trade-analysis path. `fetch_orders.py`
  writes `data/orders_raw.json` every run so the mapping can be checked once for
  real. **A mis-mapped `stopPrice` reports a protected position that isn't.**
