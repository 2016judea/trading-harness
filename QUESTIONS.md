# Questions people bring, and the tool that answers each

Per `~/.claude/CLAUDE.md`, *Every question theme gets a tool* (2026-11-22). Counts are
PostHog `mcp_tool_call`, `site = trading-harness-mcp`, all time to 2026-11-22, test
traffic included (the live suite is part of it). Themes with no call yet are marked
**inferred**: they come from what an investor or trader asks a chatbot about a stock or
their own account, not from our logs.

## From the logs

| theme | user's words | calls | tool |
|---|---|---|---|
| a company's filings | "show me Apple's financials", "10 years of revenue" | 9 (7 input errors) | `as_reported_financials` |
| rebalance to targets | "what do I buy and sell to get to 60/40" | 9 (4 input errors) | `rebalance_order_list` |
| what I made | "what did I actually make, by stock" | 9 | `realized_pnl` |
| intrinsic value | "what's Coke worth", "reverse DCF" | 8 (4 input errors) | `owner_earnings_valuation` |
| stops on my positions | "is my stop actually resting" | 5 | `stop_coverage_check` |
| which is cheaper | "Coke or Pepsi" | 4 (2 input errors) | `compare_valuations` |

Every error on record is `input`: a ToolError from a bad or missing argument. Until
2026-11-22 the log didn't say which argument, so the 17 input errors can't be split
further. **Now logged:** `err_detail` (the schema miss, our words plus the argument
name, never a pasted value) and `attempted` (any tool name a caller reached for that
doesn't exist). Read both before the next pass.

## Inferred themes, each with a door (shipped 2026-11-22)

| theme | user's words | tool | engine |
|---|---|---|---|
| over- or undervalued | "is MSFT overvalued", "intrinsic value of Deere" | `is_this_stock_undervalued` | owner_earnings_valuation |
| what the price expects | "what growth is priced into Nvidia" | `growth_priced_into_stock` | owner_earnings_valuation (reverse DCF first) |
| rank a few names | "which of my stocks is cheapest" | `which_stock_is_cheapest` | compare_valuations |
| free cash flow | "Apple's free cash flow", "10 years of FCF" | `free_cash_flow_history` | as_reported_financials, cfo - capex |
| dividends and buybacks | "can Verizon cover its dividend", "Apple buybacks" | `dividends_and_buybacks` | as_reported_financials |
| tax: short vs long term | "how much short-term gain did I have this year" | `short_vs_long_term_capital_gains` | realized_pnl lots, split at one year |
| tax: wash sales | "did I trigger a wash sale" | `wash_sale_check` | realized_pnl lots + buys within 30 days |
| tax: harvesting | "what can I tax-loss harvest" | `tax_loss_harvest_candidates` | positions export, totalGain < 0 |
| am I any good | "what's my win rate", "my worst trades" | `trading_win_rate` | realized_pnl |
| unprotected positions | "which stocks have no stop" | `positions_without_stop_loss` | stop_coverage_check |

## Gaps left, and why

| theme | user's words | why not yet |
|---|---|---|
| current price, charts | "what's AAPL trading at" | no price feed by design; the person or a cited quote supplies it |
| buy/sell call | "should I buy KO" | not advice; the valuation tools answer the question underneath |
| unrealized short vs long term | "which of my losses are long-term" | a positions export has no per-lot purchase date; needs a lot-level export |
| portfolio performance vs S&P | "did I beat the market" | needs a price history feed; same gap as the first row |
| options P&L | "what did I make on calls" | options rows are set aside (multipliers); needs a contract-aware matcher |
