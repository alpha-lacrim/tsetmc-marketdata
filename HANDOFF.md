# Current state - native share expansion, 2026-10-22

Default tsetmc-simple sends daily history and current observations directly to one
company FastAPI/PostgreSQL. No source DB or Docker required. HTTP/open access and
optional upload token are authorized. Read docs/native-install.md and the companion
company docs/native-dashboard.md. Original Foolad pilot, old DB/outbox pipeline,
migrations, archives and frozen releases are unchanged. Legacy CLI pilot fences stay.

## Current instrument scope

Owner authorized a fixed top100 default after repeated empty market-watch responses.
Default startup loads packaged data/top100.json without discovery. --all-shares opts
into the former broad mode; --ins-code (repeatable) selects explicit IDs. Latest capture: 3,462 rows, 745 eligible primary-share candidates using yVal
300/303/309/313 and IRO[1357][A-Z0-9]{4}0001. Other products and secondary lines are
excluded, not merged. IDs are validated/deduplicated and metadata is qualified per
instrument. Original special Foolad identity checks still hold. Restart rediscovers
new instruments; there is no silent runtime universe rotation.

Bulk market-watch prices are not ingestion input: date and long-form price fields
were zero placeholders. Existing GetClosingPriceInfo and full daily endpoints supply
time-aware prices and exact Decimal history. Four live sample families passed
metadata/full-history/current parsing (Foolad, Simcat, Vashahr, Mehrmam); no claims of
full 745 qualification. Evidence and counts: docs/instrument-expansion.md. Raw bodies
are private under .local/expansion-20261022/. Prior source qualification is separately
recorded in docs/validation-review-2026-10-22.md and its ignored captures.

## Scheduling and durability

One shared paced source client, independent history/latest loops, sequential visits
per stream. One request start/second default: a 745-share latest sweep takes at least
about12.5 minutes; the fixed100 takes at least100 seconds, more with metadata/history/failures. --poll-seconds is the pause
AFTER a sweep. Use a small explicit watchlist for better freshness. Do not claim
real-time/source finality, native minutes or invent clocks from receipt times.

Full daily history refreshes every86400 seconds per instrument by default. The
history-completed.json map is atomically fsynced only after all upload receipts and
survives restarts. --once forces a full pass. Partial histories may replay safely;
exact pending batches drain before new collection. Checkpoint binds to API URL, not
DB identity. After intentional company DB replacement archive old state and select
a new directory; do not discard pending files. Bad records skip that instrument and
log the error, permitting other instruments to continue. Access denials and permanent
upload errors stop. Metadata names/company/ISIN are attached, IDs remain source keys.

Upload helper is identical to company copy: exact retry bytes, receipt validation,
30..300s retry with longer Retry-After respected, visible diagnostic causes. Source
parser retains wrong-identity/conflict quarantine, exact decimals, stale clocks and
zero activity. Future source clocks >5min ahead of receipt are rejected. Official
close need not be inside executed price range. No source correctness gate was relaxed.

## Verification and next operation

306 source tests passed /119 skipped (legacy PG/live opt-in); Ruff, format, mypy,
wheel/sdist passed. New tests exercise discovery exclusions and invalid IDs,
independent two-instrument uploads, failure isolation and acknowledged history resume.
Companion full suite565 passed /365 skipped with native disposable PostgreSQL,
including >1000 source directory paging and display names. No live company writes.
Company new native100 TradingView list passed read-only bar/quote probes for100 feeds.

Commit/checkpoint both repos, rebuild ZIPs with new configs/docs, restart processes
with retained state directories. No DB reset/migration. No employer host was supplied
or deployment performed. Monitor full-history fill, errors, pending files, source
freshness and company disk growth. Read LOCAL_WORKSPACE.md/docs/workspace.md before
workspace cleanup; preserve archived original data.


## Discovery startup recovery fix

User observed no-primary-shares at startup. A fresh paced read-only probe returned
3,462 market-watch rows and 745 selected IDs; the failing response itself was not
available, so its upstream cause is unconfirmed. Normal discovery now retries
NOT_FOUND (empty/no-match list) alongside transient transport/server errors, with
60..300s backoff and row-count diagnostics. Schemas, identities and access gates are
unchanged; --once still fails immediately. Tests cover repeated emptiness/recovery,
backoff cap and fatal schema/identity/access errors. Full suite311 passed/119 skipped,
Ruff/mypy passed. No DB or retry files changed; rerun the same collector command.

## Fixed top100 default completed

Packaged data/top100.json loads through importlib.resources independent of working directory. Default never calls market watch; --all-shares is explicit opt-in. 314 tests passed/119 skipped, Ruff/mypy and offline wheel build passed. Wheel verified to include100 IDs. Tests cover default delivery without discovery. See docs/top100.md for list and qualifications.

Verification: 96 of100 instruments passed metadata identity, current-price and five-row recent-history checks. Four timed out twice: 66416853783916493, 28431095903407567, 46752599569017089, 60350996279289099. They remain selected from the genuine ranking but are not live-qualified; the collector retries their metadata on later cycles. No prices were uploaded during these probes. Full historical completeness and continuous availability were not established.

## Reliability follow-up (2026-10-25)

Empty daily history now raises a recoverable source error and is not checkpointed;
the next sweep retries while other instruments continue. Regression covers both
explicit and default watchlists. Full suite: 316 passed, 119 skipped (legacy DB
and opt-in live tests); mypy and ruff passed. Owner accepts paced latest collection.
Companion API protects dated quotes against undated replacements and exposes
history coverage; source pacing and full-history daily refresh remain unchanged.

## Validation evidence (2026-10-26)

Native collector enables rotating diagnostic JSONL under state-dir/diagnostics:
validation.jsonl plus five ~10MiB backups, one entry per flagged response. Exact
Decimal strings, source row/index, codes/severities/reasons, insCode and source
receive time preserved. History/current/targeted-daily/identity parser findings
are logged; transport failures still terminal-only. No secrets/HTTP headers logged.
No acceptance rules relaxed; native partial-history delivery is now enabled (see below). Findings reflect
our policy, not independently established source defects. See native-install.md.

Read-only live captures for 13227300125161435 (2 rejected rows) and
15521712617204216 (1 rejected row) saved in ignored
.local/validation-review-20261026/validation.jsonl for user review. No company
uploads or database changes performed. Unit suite318 passed119 skipped; ruff/mypy
passed. Tests verify exact decimal evidence and preserved rejection behavior.


## Native partial history fix

Verified whole-history rejection came from simple_collector.collect_history's
has_errors guard after the parser had already separated valid and rejected rows.
Native mode now sends only parsed valid rows, excludes conflicting dates and logs
partial delivery. All-invalid, malformed-envelope and identity failures still fail.
Each row carries metadata.history_validation (status passed/partial, accepted_rows,
rejected_rows by source index, source_rows). Validation rules and exact values stay.
Raw flagged rows remain in existing rotating local diagnostic logs. No new DB or
schema, no legacy CLI/DB policy changes. /api/records exposes the metadata; the
chart body stays unchanged. Upload checkpoint means receipt completion, not complete
market coverage. Partial snapshots refresh on the same daily schedule; source fixes
can restore excluded dates on a later full fetch. Prior stored dates are not deleted.

Regression tests: one bad old row allows2500 valid rows; conflicting duplicate date
excluded; all-invalid histories fail; a source correction restores the old date.
Full suite322 passed119 skipped; ruff/format/mypy passed. Bounded live read-only
proof: 15521712617204216 yielded5718 accepted rows and1 rejected source row; every
accepted row passed company API Batch validation. Captures under ignored
.local/partial-history-proof/. Local API was offline during the check, so this is
not a live company upload/storage qualification. Restart collector to activate.


## Slow company history uploads

Company API update verified; both active collectors targeted the right remote URL.
Latest prices reached100 instruments but history only8 per market at initial check.
TSETMC pending500rows (~551KB) took28.73s to acknowledge on the observed path;
100rows (~106KB) took3.41s. Both receipts matched. Old30s timeout was marginal;
network/server allocation of delay remains unknown (no company shell access).
Native history now uploads newest100row batches first and uses60s HTTP timeout.
Ascending order within a batch, no row omission, pending bytes and receipt checks
preserved. Corrected rows/daily refresh unchanged. No DB/API changes required.
Restart the existing company collector state directory; never delete pending files.
Full suite329passed119skipped, lint/format/mypy passed. Legacy PG/live opt-in suites
remain skipped. No sustained company full-universe qualification claimed.


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
