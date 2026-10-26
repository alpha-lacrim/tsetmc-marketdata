# Native collector installation (no Docker or source database)

This is the default path for new installations. The collector sends available daily
history and latest observations directly to the shared company FastAPI service.
It keeps a small durable retry file instead of a local PostgreSQL database.

First install the company API using the companion repository's
[Ubuntu installation guide](https://github.com/alpha-lacrim/tradingview-marketdata/blob/main/docs/native-install.md).
Only that server needs PostgreSQL. On this collector host:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv python install 3.12
mkdir -p "$HOME/marketdata"
cd "$HOME/marketdata"
git clone https://github.com/alpha-lacrim/tsetmc-marketdata.git
cd tsetmc-marketdata
uv sync --frozen --no-dev --python 3.12
export COMPANY_API_URL='http://SERVER:8000'
uv run --no-sync tsetmc-simple
```

The default loads a **fixed top-100 watchlist bundled with the installed package**.
It does not call market watch during startup, so an empty market-watch response
cannot block normal collection. The selection uses reported traded value for primary
ordinary shares in a captured TSETMC snapshot, with Foolad included. It is a dated
liquidity shortlist, not an official index, market-cap ranking or investment advice.
See [the named list and endpoint checks](top100.md).

Run `uv run tsetmc-simple --state-dir .local/laptop-test-tsetmc` on the existing
laptop. Stop the previous copy first. **Remove `--all-shares` from the old command.**
Keep the same state directory and company database. Explicit `--ins-code` values
still select a smaller watchlist. `--all-shares` is optional live discovery of the
broader primary-share universe; its empty-response retry behavior remains available.

Requests share one paced HTTP client (one request start per second by default).
Latest-price sweeps run sequentially, followed by a 15-second pause. With 100
instruments a sweep takes at least about 100 seconds and usually longer, longer during initial qualification/backfill
or source failures; `--poll-seconds 15` does NOT mean every share updates in 15 seconds.
For a faster small watchlist, repeat `--ins-code`:

```bash
uv run tsetmc-simple --ins-code 46348559193224090 --ins-code 41379697187196382
```

Full history refreshes per instrument every 86,400 seconds by default, preserving
old corrections. `history-completed.json` records upload completion only after all
accepted batches are acknowledged; this does not certify complete source history.
Partial snapshots follow the same daily refresh schedule, so corrected source rows
are reconsidered on the next refresh. A restart does not redownload acknowledged snapshots. Never delete
pending files. Retain the checkpoint with the same company database; after an intentional
company database reset, archive the state directory and start a new one. The checkpoint
is bound to the API URL but cannot detect a database reset behind that same URL.
`--once` forces a full pass regardless of history checkpoints and reports any failed
instrument as failure after attempting the remaining instruments in that stream.
Use `--history-seconds 3600` for hourly full history if that source load is acceptable.
Metadata names are sent to the company API. Source IDs remain the storage keys.

Malformed historical rows and conflicting duplicate dates are excluded while valid
rows continue uploading with explicit partial-history metadata. A wholly malformed
response, no valid rows, or invalid instrument identity still fails that instrument;
other instruments continue. Access denials and permanent upload failures still stop.
No source price is derived from the abbreviated bulk market-watch fields: the observed
bulk response lacks a usable date and contains zero-valued placeholder price fields.
See [coverage and live checks](instrument-expansion.md).

Keep `.local/simple-tsetmc/` across restarts. A pending upload is retried before
new observations for that stream; malformed/conflicting history is reported and
not silently uploaded as complete. Source parsers, decimals and original timestamps
are preserved. Last trade and official close remain distinct. History finality and
source units are not promoted to verified status by successful delivery.
The collector prints payload transfer totals every minute and saves cumulative byte
counters in `<state-dir>/traffic.json`. `source_received` counts decoded TSETMC HTTP
response bodies, including failed responses and retries. TSETMC GET requests have no
request body, so `source_sent` is normally zero. `api_sent` counts submitted upload
bodies, including retries, and `api_received` counts response bodies. The file survives
restarts. These are application payload estimates, not billed network bytes: HTTP
headers, compression, TLS, connection setup and retransmissions are excluded. A
failed upload can send less than the submitted body counted here.

HTTP is accepted, no token is required by default, and company DB credentials are
never needed here. If the API operator sets `SIMPLE_UPLOAD_TOKEN`, set the same
environment variable on this collector. Anyone who can reach an API with no token
can upload records. Browser reads remain open.

For automatic startup, use `deploy/native/tsetmc-collector.service` and the user
systemd instructions in the company guide. The unit keeps retry files under
`$HOME/.local/share/marketdata/tsetmc/`. Stop the foreground copy before starting it.
No service is installed automatically by running the Python command.

The frontend uses the company API:

```text
GET /api/chart?market=tsetmc&symbol=46348559193224090&interval=1d
GET /api/latest?market=tsetmc&symbol=46348559193224090
```

Chart output is the supplied Go contract: a=time, b=open, c=official close,
d=high, e=low, f=count, g=volume, h=traded value, i=null. Unknown activity is a
zero placeholder only in that response. Exact records and nulls remain stored.
TSETMC does not supply native minute history in this collector.

Existing PostgreSQL collection/outbox/delivery commands remain available for old
installations. Their databases and the original pilot are preserved; the new API
does not automatically import their archived data. See [company-delivery.md](company-delivery.md)
only if continuing that older architecture.

## Failure handling and troubleshooting

Uploads retain the exact pending batch until a matching receipt arrives. Transient
network errors, HTTP 408/429 and server 5xx responses retry after 30 seconds,
doubling up to five minutes. A longer numeric or HTTP-date Retry-After is honored.
Logs identify the failure type/status, pending filename, retry delay and recovery.
Do not delete pending files. Bad receipts and permanent upload rejections stop
rather than silently discarding data; check the API URL/token/schema and restart
with the same state directory after resolving the cause.

If the API stops responding on Windows, press Esc in its terminal to clear any
console text selection, then check /health and /openapi.json. Selection can block
console output; it is not the only possible cause of a hang. HTTP access logging
is off by default to reduce terminal output; enable `marketdata-simple serve
--access-log` when diagnosing requests. Startup and error logs remain enabled.
Database pool waits are bounded at five seconds and native database operations at
20 seconds. Handled database/network/timeouts return HTTP 503 with Retry-After: 30.
An unresponsive process/network can still time out without returning JSON.
WebSockets close with code 1013 on temporary database failures; reconnect with
the last processed cursor. These changes do not alter the chart response format.

TSETMC drains already staged uploads before its startup identity lookup. Temporary
network/DNS/timeout/server lookup failures retry at 60 seconds, increasing to five
minutes. Invalid instrument identities skip that instrument; access denials stop and are never bypassed.
Normal polling reports the error category and status instead of only an exception
class. `--once` continues to exit unsuccessfully on source failures.

## Source validation

See [the 2026-10-22 behavior review](validation-review-2026-10-22.md) for live
Foolad endpoint comparisons and validation changes. Current responses can carry
an older source date even when polling succeeds. The source clock is not a
verified last-trade timestamp. Keep machine clocks synchronized: source event
times more than five minutes ahead of receipt are rejected by local policy.


Discovery recovery: an empty/no-match market-watch response now logs the number
of rows received and retries after 60, 120, 240, then 300 seconds (capped). It does
not start an empty universe or loosen identity/product filters. Malformed schemas,
invalid identities and access denials still fail visibly. `--once` fails immediately
on unsuccessful discovery. The reported no-share response was not captured; a later
bounded probe returned 3,462 rows and 745 accepted share IDs. Its cause is unconfirmed.

## Empty history retries

An empty daily-history response is treated as a failed attempt, not a completed
download. The next history sweep retries it; other instruments continue. There
is no 24-hour completion checkpoint until nonempty history is delivered.
Latest prices remain paced per-instrument observations, not synchronous snapshots
of all shares. Use the API event/receipt ages when showing dashboard freshness.

## Inspect flagged source data

The native collector writes JSON Lines to
`<state-dir>/diagnostics/validation.jsonl`, with five rotated backups (.1 through
.5), rotating at approximately 10 MiB each (a single large entry may exceed it).
Each line includes instrument ID, source endpoint path, receive timestamp,
severity/code, rejection reason, and original flagged rows. Decimal values are
stored as exact strings; dates and conflicting duplicates remain inspectable.
History warnings/informational parser findings and current-price diagnostics are
also logged. Schema/identity parser failures retain the decoded response payload.
Transport failures remain in the terminal; this is not a raw HTTP traffic capture.
These files stay local/ignored and are not included in Git or deployment ZIPs.
Older rotated evidence eventually expires; copy files elsewhere if needed.

For your laptop:

```powershell
Get-Content .local/laptop-test-tsetmc/diagnostics/validation.jsonl -Tail 5
Get-Content .local/laptop-test-tsetmc/diagnostics/validation.jsonl |
    ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object instrument -eq '15521712617204216' |
    ConvertTo-Json -Depth 20
```

Restart the collector to enable logging. Flagged does not prove incorrect source
data: it means our current rule rejected it. Validation rules remain unchanged. Native history delivery now uploads valid rows
while retaining flagged rows in these logs; conflicting duplicate dates are omitted.
Current-price, identity and response-schema errors still fail their observation.


## Partial history delivery

Every daily observation includes `metadata.history_validation`:

```json
{"status":"partial","accepted_rows":2500,"rejected_rows":1,"source_rows":2501}
```

`rejected_rows` counts distinct source row indexes flagged as errors, not missing
trading dates. Conflicting duplicates can count multiple rows for one omitted date.
Identical duplicates are deduplicated without making the snapshot partial. A
`passed` snapshot means all rows passed our rules; it is not proof of complete
provider history, finalized prices or complete exchange-session coverage.
Inspect this metadata through `/api/records`. Chart JSON remains unchanged.
Console output identifies partial snapshots before collection upload and after
acknowledgement. The raw flagged rows/reasons remain in rotating diagnostic files;
copy them elsewhere for retention beyond rotation. No invalid prices are fabricated
or repaired. Previously stored observations are not deleted if a later source
snapshot flags that date. Legacy export/DB pipeline policies are unchanged.

Restart the native collector with the same state directory to load this change.
Previously failed histories have no successful upload checkpoint and are retried;
previously acknowledged snapshots refresh normally. `--once` forces a full pass.
No API/server upgrade or database migration is required for this metadata addition.


## Remote upload pacing

History uploads now contain at most100 records, newest batches first (rows within
a batch stay chronological); the upload timeout is60seconds. Older history still
uploads afterward. Existing pending batches are retried unchanged before collecting
new ones. No state reset, server update or database migration is needed. Preserve
.local/company-tsetmc and restart the collector to load the new behavior.
Company history can remain incomplete during initial fill: start=0 shows all stored
dates while the default chart range shows only the last30days. This is separate
from partial-history validation status. Diagnostic/source validation rules remain.


Recent-first startup: normal continuous collection uploads up to100 newest valid
daily rows for each unfinished instrument before deep history. Preview rows have
history_delivery.scope=recent_preview, snapshot_rows, uploaded_rows and
full_upload_pending=true. This does not set the completion checkpoint; a subsequent
full accepted upload does. --once bypasses the preview and remains a full pass.
Existing pending files drain before this preview without modification. Initial
source requests still fetch full daily snapshots for parsing/validation; upload
volume is reduced for the first pass. Deep history can still take hours and preview
rows do not establish complete coverage. Full suite330passed119skipped; a regression
checks two instruments get recent rows first, then every older row, with completion
checkpoints only after full acknowledgements. Lint/format/mypy passed.
