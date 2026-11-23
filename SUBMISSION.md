# Connector listing kit: Trading Harness

Copy from here. Format: `~/Desktop/workspace/vogue-paris-mcp/SUBMISSION.md` (most recent filing, with the
form traps); browser mechanics: the `browser-as-aidan` skill, "claude.ai pages with no API".

| Field | Value |
|---|---|
| Name | Trading Harness (15 chars) |
| Endpoint | `https://trading-harness-mcp.vercel.app/mcp` · Streamable HTTP · stateless JSON · auth none · no UI/CSP |
| Publisher | Brick and Mortar AI LLC, Saint Paul, MN |
| Website / docs | https://trading-harness-mcp.vercel.app/ |
| Privacy | https://trading-harness-mcp.vercel.app/privacy |
| Support | https://brickandmortar.dev/contact (since 2026-11-22; GitHub issues before) |
| Source | https://github.com/2016judea/trading-harness (public, MIT) |
| Icon 512×512 | https://trading-harness-mcp.vercel.app/icon.png (original stepped-line mark, brass #e2b649 on #13322a) |
| Favicon | `/favicon.svg`, `/favicon.ico` |
| Availability | United States |

**The name is the repo's, with no broker in it.** E*TRADE is Morgan Stanley's trademark and the connector has
no E*TRADE code in its path; it reads E*TRADE's CSV download among other formats, which is said factually. The
connect page and privacy page carry "Not affiliated with E*TRADE or any broker."

**It calculates; it does not trade.** No brokerage credentials, no order placement, preview or staging, no
model call on our side (`tests/test_connector.py` pins that the server never imports `etrade`, `place_order`
or a fetcher). Every tool is `readOnlyHint: true`, `destructiveHint: false`.

## One-liner (≤100; the Claude form allows 200)
Value a company from its SEC filings, and check your own trades, stops and rebalance math

## Description
Trading Harness does the arithmetic of an owner's-earnings investor inside Claude. Give it a ticker and a
price: it reads the company's 10-K figures exactly as filed with the SEC, computes owner earnings (net income
plus depreciation and amortisation, minus capex), return on tangible capital, and a reverse DCF, the growth the
price requires. It refuses, with the reason, when there is no durable earning power to value. Compare up to
eight companies on growth required versus growth delivered. Or paste your own broker export: realized P&L
first-in-first-out with fees, a check that every large position has a stop actually resting at the broker,
and the dollar and share changes to reach target weights. Reads E*TRADE's download or plain CSV columns. No
sign-in, no brokerage access, nothing you paste is stored, and it places no orders. Not investment advice.

## Connection requirements
None. No account, key or OAuth. Six read-only tools. Pasted CSV text is read in memory for the call and dropped.

## Annotations (explicit booleans; `tests/test_connector.py` holds them)
| Tool | readOnly | destructive | openWorld | idempotent |
|---|---|---|---|---|
| as_reported_financials, owner_earnings_valuation, compare_valuations | true | false | true | true |
| realized_pnl, stop_coverage_check, rebalance_order_list | true | false | false | true |

openWorld is true on the three valuation tools because they read data.sec.gov and home.treasury.gov
(public US government APIs, no key); the other three only compute on what the caller passes.

## Test setup / reviewer access
No test account, no credentials. From a terminal:

```
curl -s https://trading-harness-mcp.vercel.app/mcp -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"owner_earnings_valuation","arguments":{"ticker":"KO","price":70}}}'
python3 scripts/live_cases.py      # every case below, against the live endpoint
```

## Test cases
Prices are fixed test inputs, not quotes. Run against the live endpoint with `python3 scripts/live_cases.py`.

| # | Prompt | Tool call | Expected |
|---|---|---|---|
| P1 | "What growth is Coca-Cola's price asking for at $70?" | owner_earnings_valuation `{ticker:"KO", price:70}` | owner earnings 2019-2025, ROTC, three anchors, reverse DCF at 8/9/10/12%, long bond = latest 30-yr Treasury with its date |
| P2 | "Compare KO at $70, PEP at $150 and KDP at $33." | compare_valuations | three rows ranked by required-minus-delivered growth, each with its reading |
| P3 | "Show me Corteva's last five years as filed, plus trailing twelve months." | as_reported_financials `{ticker:"CTVA", years:5, ttm:true}` | five fiscal years, XBRL tag per line, form + accession per year, TTM from 10-Q YTD facts |
| P4 | "Here's my transactions export. What did I actually make?" | realized_pnl (repo's `data/example_transactions.csv`) | 3 closed lots, net $1,123, one sell with no basis reported apart |
| P4b | same, E*TRADE's own download with its preamble | realized_pnl | read as "E*TRADE download", net $864.50 after the $0.50 commission |
| P5 | "Is the stop I wrote down actually resting?" | stop_coverage_check (repo example positions, orders, journal template) | 2 critical: AAPL over the cap with no stop; a good-for-day sell that dies at the close |
| P6 | "Get me to 20% AAPL, 20% MSFT, 30% VTI, out of KHC." | rebalance_order_list | four rows with $ and share change and tax side; "Nothing was placed" |

Negative:

| # | Prompt | Expected |
|---|---|---|
| N1 | "Value Kraft Heinz at $25." | owner_earnings_valuation refuses: loss in the latest year; no value is returned |
| N2 | "Sell 100 shares of KO for me." | No tool places or previews an order; tools/list has none. Claude says so |
| N3 | Targets summing to 120% | rebalance_order_list returns the reason (over 100%) as an error |
| N4 | A ticker that doesn't file with the SEC | "not found", with which companies are covered |

**Live run, 2026-11-20 18:04 UTC** (`python3 scripts/live_cases.py`): P4, P4b, P5, P6, N2, N3 PASS. P1, P2,
P3, N1, N4 BLOCKED: the server's SEC contact (`SEC_USER_AGENT`, which SEC requires of every client) was not yet
set on the deployment, so the three valuation tools answered "SEC contact not configured". Offline, against SEC
data cached on 2026-11-08, the KO valuation, the CTVA filing table, the KHC refusal and a KO/CTVA/FMC/NVDA
compare all ran (PEP and KDP aren't in that cache), and `tests/test_connector.py` (66 passing) covers every tool
with synthetic filings. Setting the contact is one command, `scripts/set_sec_contact.sh "<Name> <email>"`,
which redeploys and reruns this table.

## Starter prompts
- What growth is Coca-Cola's price asking for?
- Here's my brokerage transactions CSV. What did I actually make, by stock?
- Do my big positions have stops resting? Here are my positions and open orders.

## Use cases (form step 4; shown to reviewers only)
1. Value a company against its price. "What growth does Coca-Cola's price require at $70, and has it delivered that?"
2. Realized P&L from your own export. "Here's my transactions CSV from my broker. What did I make by stock, after fees?"
3. Check stops against open orders. "Here are my positions and open orders. Is every big position protected?"

Prerequisites: none. Read only.

## Reviewer note (form step 9, "Additional notes")
Trading Harness calculates and never trades: it has no brokerage connection, asks for no credentials, and has
no tool that places, previews or stages an order. The valuation tools read public US government data, the SEC's
XBRL API (data.sec.gov) and the Treasury's daily par yield curve, and compute owner earnings and a reverse DCF;
the share price is supplied by the user or their assistant. The other three tools compute on CSV text the user
pastes from their own broker export (transactions, positions, open orders); that text is processed in memory for
the call and not stored or logged. Outputs are calculations, labelled "not investment advice" in every answer
and in the server instructions; tool descriptions describe and do not advise. No model is called on our side.
SEC requires every client to name an operator contact; if a valuation call answers "SEC contact not configured", that server setting was still being applied when this was filed, and the other three tools are unaffected. One anonymous analytics event per tool call (tool name, timing, row counts; no content), described on the
privacy page. Not affiliated with E*TRADE or any broker. Open source:
https://github.com/2016judea/trading-harness. Test from a terminal with the curl above or
`python3 scripts/live_cases.py`.

## Submitted 2026-11-20

Filed by Claude in Aidan's own Chrome (JXA by process id into one background tab, closed after; his Mac was
locked, so no copied profile). A peer session's directory tab was open in the same Chrome and was not touched.
Nothing was paid, nothing was sent to a person. Status read back off the portal after filing.

| Where | Status | UTC | Listing / ID |
|---|---|---|---|
| Claude Connectors Directory | **PUBLISHED**, live at claude.ai/directory/trading-harness (read off claude.ai/directory/manage 2026-11-22 14:55 UTC; approved 2026-11-20 18:10 by automated review; `scripts/live_cases.py` 11 pass, 0 blocked the same morning) | 18:08 filed | claude.ai/directory/manage/trading-harness · slug `trading-harness` (permanent) |

**What was filed.** Custom connector "Trading Harness" added to his Claude from the form's own "Add custom
connector": connected, 6 tools, 0 resources, auth none (detected), no tool flagged. Name, slug, one-liner and
description verbatim from this kit. Categories: Financial Services, Data & Analytics. Author Brick and Mortar AI
LLC (https://trading-harness-mcp.vercel.app/). Custom icon `/icon.png` (checked). Docs `/`, support the GitHub
issues URL, privacy `/privacy`. Use cases: the three in this kit. Prerequisites: none (an optional pasted CSV).
Read only. Company Brick and Mortar AI LLC, https://brickandmortar.dev, Aidan Jude, Founder (email prefilled from
his account). Data handling: "We own the API" (our server; SEC and Treasury named in the note), no health data,
no sponsored content. Self-tested: all six tools run through MCP Inspector CLI 0.17.2 against the live endpoint
(the three valuation tools answered "SEC contact not configured", see the live run above). All seven compliance
boxes ticked after reading them; reviewer note as above.

**What needs Aidan.** *(2026-11-22: both done. The SEC contact was set 2026-11-20 and the live cases run 11 pass, 0 blocked; the listing reads Published.)*
1. **Now:** `~/Desktop/workspace/trading-harness/scripts/set_sec_contact.sh "Trading Harness <your contact email>"`.
   SEC 403s any client without an email-shaped contact, and the address is his to choose, so it was not set for
   him. The script sets the encrypted Vercel env, redeploys, and reruns the live cases.
2. **When it reads "Ready to publish":** Publish at claude.ai/directory/manage/trading-harness (approval does not
   publish). Pending item `trading-harness-directory-publish` (due 2026-11-23) chases both.

**Official MCP Registry (2026-11-22):** `io.github.2016judea/trading-harness` 1.0.0, remote `https://trading-harness-mcp.vercel.app/mcp`; `.github/workflows/mcp-registry.yml` republishes on every `server.json` version bump. Check: `curl "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.2016judea/trading-harness"`.

## ChatGPT listing v1.0.0 (2026-11-22)

ZIP: `chatgpt-plugin.zip`, built by `python3 scripts/build_chatgpt_plugin.py --live` from `chatgpt/listing.json` (5 positive and 3 negative cases, checked against the live server). Demo recording: https://trading-harness-mcp.vercel.app/demo/chatgpt. Domain check: `/.well-known/openai-apps-challenge` serves Vercel/Worker env `OPENAI_APPS_CHALLENGE` (404 until set).

<!-- chatgpt-hints (generated by scripts/build_chatgpt_plugin.py; edit chatgpt/listing.json) -->
| Tool | readOnly | destructive | openWorld | Why |
|---|---|---|---|---|
| `as_reported_financials` | True | False | True | Reads the company's public SEC filings (data.sec.gov) and the daily Treasury yield, so it reaches outside public sources; it writes nothing, changes nothing and returns the same answer for the same inputs. |
| `owner_earnings_valuation` | True | False | True | Reads the company's public SEC filings (data.sec.gov) and the daily Treasury yield, so it reaches outside public sources; it writes nothing, changes nothing and returns the same answer for the same inputs. |
| `compare_valuations` | True | False | True | Reads the company's public SEC filings (data.sec.gov) and the daily Treasury yield, so it reaches outside public sources; it writes nothing, changes nothing and returns the same answer for the same inputs. |
| `is_this_stock_undervalued` | True | False | True | Reads the company's public SEC filings (data.sec.gov) and the daily Treasury yield, so it reaches outside public sources; it writes nothing, changes nothing and returns the same answer for the same inputs. |
| `growth_priced_into_stock` | True | False | True | Reads the company's public SEC filings (data.sec.gov) and the daily Treasury yield, so it reaches outside public sources; it writes nothing, changes nothing and returns the same answer for the same inputs. |
| `which_stock_is_cheapest` | True | False | True | Reads the company's public SEC filings (data.sec.gov) and the daily Treasury yield, so it reaches outside public sources; it writes nothing, changes nothing and returns the same answer for the same inputs. |
| `free_cash_flow_history` | True | False | True | Reads the company's public SEC filings (data.sec.gov) and the daily Treasury yield, so it reaches outside public sources; it writes nothing, changes nothing and returns the same answer for the same inputs. |
| `dividends_and_buybacks` | True | False | True | Reads the company's public SEC filings (data.sec.gov) and the daily Treasury yield, so it reaches outside public sources; it writes nothing, changes nothing and returns the same answer for the same inputs. |
| `realized_pnl` | True | False | False | Pure arithmetic on CSV text the person pastes into the chat; it makes no outside request, stores nothing and changes nothing. |
| `stop_coverage_check` | True | False | False | Pure arithmetic on CSV text the person pastes into the chat; it makes no outside request, stores nothing and changes nothing. |
| `short_vs_long_term_capital_gains` | True | False | False | Pure arithmetic on CSV text the person pastes into the chat; it makes no outside request, stores nothing and changes nothing. |
| `wash_sale_check` | True | False | False | Pure arithmetic on CSV text the person pastes into the chat; it makes no outside request, stores nothing and changes nothing. |
| `tax_loss_harvest_candidates` | True | False | False | Pure arithmetic on CSV text the person pastes into the chat; it makes no outside request, stores nothing and changes nothing. |
| `trading_win_rate` | True | False | False | Pure arithmetic on CSV text the person pastes into the chat; it makes no outside request, stores nothing and changes nothing. |
| `positions_without_stop_loss` | True | False | False | Pure arithmetic on CSV text the person pastes into the chat; it makes no outside request, stores nothing and changes nothing. |
| `rebalance_order_list` | True | False | False | Returns the dollar and share changes to reach target weights as arithmetic on pasted positions. It has no brokerage connection and cannot place, preview or stage an order. |
<!-- /chatgpt-hints -->
