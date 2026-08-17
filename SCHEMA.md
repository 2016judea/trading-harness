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
