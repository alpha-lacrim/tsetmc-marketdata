# TSETMC intraday feasibility: 26 October 2026

## Conclusion

Subdaily data is available from the official site's HTTP APIs. A bounded research
probe successfully derived 1m, 5m and 15m trade candles and reconciled them against
native daily records. This is feasibility evidence, not a deployed feature or a
guarantee of complete history for all instruments/dates. The native collector still
publishes daily history and latest observations only.

No HTML table scraping, third-party provider, credentials or access-control bypass
was needed. We inspected the JavaScript served by the official instrument page and
used the same public API routes. These are observed website interfaces, not a
versioned, documented service contract.

## Website and endpoints

Official page: https://www.tsetmc.com/instInfo/46348559193224090

The downloaded application asset was
`https://www.tsetmc.com/main.f24603b0cc21e2598771.js` (5,636,885 bytes), SHA256
`3ba4d37ba96ac038995fdfd251b5bff6f68a50bb3f907647da6bf59f1e65d351`.
An earlier asset name returned an HTML application fallback despite HTTP 200;
content must be checked, not just status.

All paths below are relative to `https://cdn.tsetmc.com/api/`.

| Route | Website usage and measured result |
| --- | --- |
| `Trade/GetTrade/{insCode}` | Current-session individual trades; Foolad returned 17,484 rows. Count, volume and traded value matched the separately fetched current summary. |
| `Trade/GetTradeHistory/{insCode}/{YYYYMMDD}/false` | Full trade table loaded by the site's historical Trades tab. Recommended candidate for dated reconstruction. |
| `Trade/GetTradeHistory/{insCode}/{YYYYMMDD}/true` | Used in the site's initial composite historical view. Returned only 6,575 rows versus 28,924 with `false` for Foolad on 20261025. Do not treat this variant as complete; the boolean's exact server-side meaning is unconfirmed. |
| `Trade/GetTradeIntraDay/{insCode}` | Current compact OHLCV table: 105 rows and 11,677 response bytes. Times were not aligned minute-start timestamps. Its volume matched current trades, but naive wall-clock 2m grouping produced 106 rows. Interval boundary/label semantics remain unqualified. |
| `Trade/GetTradeIntraDayCSV/{insCode}/{interval}/{symbolName}` | Site download controls pass 2 and 30 as interval arguments. Call signature verified in JavaScript; CSV contents were not probed. No historical date argument was observed. |
| `ClosingPrice/GetClosingPriceHistory/{insCode}/{YYYYMMDD}` | Sample returned only one pre-session, zero-activity summary. Not a reliable replacement for actual trade history. |
| `ClosingPrice/GetChartData/{insCode}/{resolution}` | Site chart lists subdaily resolutions, but symbol configuration has `has_intraday:false`. Sample `15` response was byte-identical to `D`; `5` returned 92,550 rows. All three had negative `dEven` values unsuitable for accepting as modern Unix timestamps without further qualification. Do not infer valid candle support from the resolution menu. |

## Reconciliation results

Trades were sorted by `(hEven, nTran)`, with `canceled=1` excluded. Prices and value
arithmetic used Decimal. For each successful sample, active count, volume, value,
first trade, last trade, minimum and maximum all matched
`ClosingPrice/GetClosingPriceDaily/{insCode}/{date}` exactly. The close comparison
uses `pDrCotVal` (last trade), not `pClosing` (official daily close).

| Instrument | Date | Returned rows | Cancelled rows | Active trades | 1m / 5m / 15m nonempty buckets | Daily reconciliation |
| --- | --- | ---: | ---: | ---: | --- | --- |
| Foolad `46348559193224090` | 2026-10-25 | 28,924 | 0 | 28,924 | 210 / 42 / 14 | All seven checks passed |
| Foolad | 2026-09-26 | 31,636 | 0 | 31,636 | 211 / 43 / 15 | All seven checks passed |
| Foolad | 2025-10-26 | 8,383 | 14 | 8,369 | 210 / 42 / 14 | All seven checks passed |
| Foolad | 2021-10-26 | 10,262 | 0 | 10,262 | 210 / 42 / 14 | All seven checks passed |
| `10979459081222858` | 2026-10-25 | 29,517 | 0 | 29,517 | 209 / 42 / 14 | All seven checks passed |
| `58931793851445922` | 2026-10-25 | 25,744 | 10 | 25,734 | 210 / 42 / 14 | All seven checks passed |
| Foolad | 2007-03-11 | 0 | — | — | — | Daily record has 7,736 trades: intraday archive unavailable in this response |

Dates for the older Foolad probes were selected from its actual daily history.
These samples establish availability on selected dates, not continuous five-year
coverage. No archive start date or retention guarantee was established. Empty
intraday responses must not be interpreted as zero activity when daily totals are
positive. Do not force a fixed candle count: trades exactly at a wall-clock boundary
can form another nonempty bucket.

The website explicitly renders `canceled=1` trades with a strike-through and labels
them cancelled. Sample cancellation records reused trade sequence numbers and had
later times; active records had unique sequence numbers after excluding cancelled
rows. Therefore deduplicating by sequence before handling cancellations is unsafe.
`iAnuTran` alone did not identify the cancelled sample rows.

## Recommended implementation

1. Add an opt-in intraday path using dated full trade snapshots (`false`). Start
   with a small watchlist and a bounded recent-date backfill, then expand after
   measuring traffic and reconciliation failures. Continue existing daily/latest
   collection independently.
2. Validate the envelope, requested identity/date, HHMMSS, prices, quantities,
   cancellation flags and duplicate active sequences. The sampled trade rows had
   null `insCode` and zero `dEven`; bind them to the qualified request context.
   For current-session data, verify the session date against source metadata and
   protect against rollover between requests. Never use local receipt date as an
   unexplained replacement for source trading date.
3. Build actual-trade OHLC, count, volume and value with Decimal. Interpret source
   clock in `Asia/Tehran` on the source trading date, then emit UTC epoch seconds.
   Preserve the date-aware timezone's historical offset rules. Derive 5m and 15m
   from the same trades or validated 1m bars using explicit wall-clock boundaries.
   Leave no-trade gaps empty; do not fabricate flat candles.
4. Preserve snapshot provenance and validation status. Reconcile completed days
   against native daily count/volume/value and trade OHLC. A changing current day
   requires an explicit provisional status; separate requests can race.
5. Refresh recent completed sessions for cancellations/corrections. Recompute
   affected buckets, including removal of a previously stored bucket if all its
   trades were cancelled. Simple upsert-only corrections cannot handle that case.
   Do not declare a snapshot complete merely because HTTP returned 200.
6. Extend company validation, persistence/correction handling, API interval
   selection and tests together. Keep native daily official-close semantics
   distinct from intraday trade-close semantics. Existing `a` through `h` chart
   arrays can represent derived intraday candles without changing frontend keys.

Traffic matters: the full Foolad 20261025 trade response alone was 5,787,603 decoded
body bytes, and the current snapshot was 3,492,900 bytes. Repeated full snapshots
for 100 instruments would be expensive. Use pacing, bounded concurrency, cached
completed-day work and limited refresh windows; do not poll every full archive on
each latest-price sweep. Sizes are application payload, not billed network traffic.

## Evidence and scope

Paced, sequential, read-only probes and research-only aggregation ran locally on
2026-10-26. Raw bodies, request manifests, SHA256 hashes, scripts and derived bars
are ignored under `.local/intraday-research-20261026/`; they are not public fixtures
or shipped collector code. No source/company database writes, collector restarts,
API changes or deployment occurred. Existing runtime checks/tests from other work
do not establish intraday production readiness.
