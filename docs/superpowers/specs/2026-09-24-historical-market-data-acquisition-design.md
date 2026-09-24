# Historical Daily Market Data Acquisition Design

## Purpose

Build the first production-data increment for the Quant Desk KR point-in-time backtest: a resumable, auditable collector for daily full-market KOSPI and KOSDAQ data. The first target is the three-year performance window plus at least 252 earlier trading sessions for factor warm-up. The storage and validation contracts must extend to ten years without redesign.

This increment prevents survivorship bias at the source. It preserves every security present in each historical daily response, including securities that later disappear, and never filters a historical day through today's listing. It does not make the real-data backtest executable by itself.

## Scope

This increment delivers:

- official-source-first acquisition of daily full-market KOSPI and KOSDAQ data;
- immutable, versioned raw archives;
- normalization and strict validation;
- resumable background collection jobs;
- quarantine, promotion, and provenance records in SQLite;
- import of official CSV files through the same pipeline;
- Streamlit controls and diagnostics for collection jobs;
- deterministic rebuilding of normalized data from raw archives.

Later increments will add historical security classification and listing life cycles, delisting outcomes and identifier changes, corporate-action and adjusted-price validation, KOSPI and KOSDAQ indices, and point-in-time annual OpenDART statements. Until those dependencies are complete, data from this increment cannot unlock an official backtest.

## Source Policy

The collector prefers free official data. The primary market source is the KRX Data Marketplace daily all-issues data, which exposes security identifiers, market labels, OHLC, volume, trading value, market capitalization, and listed shares. OpenDART remains a separate source for a later financial-statement increment.

An automatic adapter may use only a permitted and stable official download route. It must apply conservative pacing and must not bypass access controls. If an official automatic route is unavailable, unstable, or not permitted, the workflow falls back to importing an official downloaded CSV. Third-party data is not silently substituted.

Source references:

- [KRX all-issues prices](https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd?screenId=MDCSTAT015)
- [OpenDART disclosure API guide](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001)

## Architecture

The acquisition path is deliberately separate from the engine-facing backtest tables:

```text
KRX automatic download or official CSV import
                    |
                    v
         immutable raw archive
                    |
                    v
       parse and normalize to staging
                    |
                    v
      structural and anomaly validation
              |              |
              v              v
          promoted       quarantined
              |
              v
       canonical SQLite daily data

Historical security life-cycle enrichment (next increment)
                    |
                    v
Existing point-in-time backtest interfaces
```

The first increment does not write directly to the existing engine-facing `market_snapshots` or price cache. This firewall prevents incomplete security classification, unverified adjusted-price conventions, or unresolved delistings from entering a backtest. A later adapter will transform fully enriched canonical rows into the existing point-in-time interfaces without changing the engine.

## Components

### Daily Source Adapter

`KrxDailySource` retrieves one requested date and returns the exact response bytes plus source metadata. `OfficialCsvSource` accepts a user-selected official file and emits the same result contract. Downstream code cannot distinguish automatic from manual acquisition except through recorded provenance.

### Raw Archive

`RawArchive` writes downloads to a temporary `.part` file, calculates SHA-256, and atomically renames a completed file. Raw files are immutable and versioned under a logical path such as:

```text
data/raw/krx/daily/YYYY/YYYY-MM-DD/<fetched-at>-<sha256>.csv.gz
```

A repeated download with the same checksum reuses the existing version. A different checksum creates a new version and triggers quarantine; it never overwrites the prior source.

### Normalizer

`DailyMarketNormalizer` converts provider column names into a canonical schema while retaining the raw archive. Codes remain six-character strings so leading zeroes survive. The canonical row contains:

- trade date;
- security code and name;
- KOSPI or KOSDAQ market label and available market segment;
- open, high, low, close;
- volume and trading value;
- market capitalization and listed shares;
- source batch identifier.

Missing opens are preserved as missing values rather than replaced by closes. The normalizer does not infer historical security type from a current listing.

### Validator

`DailyMarketValidator` performs hard structural checks and cross-day anomaly checks. It produces machine-readable findings with severity, rule identifier, observed value, threshold, and evidence.

### Ingestion Store

SQLite stores collection jobs, per-date attempts, raw versions, validation findings, and canonical daily rows. Schema versions are explicit. Every canonical row links to the exact raw version and normalizer version that produced it.

### Promotion Service

`PromotionService` promotes one complete date in a single SQLite transaction. Validation failures cannot leave a partly replaced day. Promotion means the daily acquisition record is canonical; it does not mean that the day is eligible for the official backtest.

### Historical Worker

A separate local Python worker processes a job one date at a time so Streamlit reruns do not destroy progress. It observes persisted pause and cancellation requests, commits each completed date independently, and resumes from the first unresolved date. A command-line entry point uses the same orchestration for recovery and testing.

## Job and Date States

Each requested date moves through explicit states:

```text
pending -> downloading -> downloaded -> parsed -> validated -> promoted
                                      \-> failed
                                                 \-> quarantined
```

Weekends are excluded before collection. A weekday with no market data is not automatically declared a holiday. After retries it becomes `calendar_unresolved` unless a recorded official-calendar source establishes it as a non-session. All requested dates must end as `promoted` or evidence-backed `non_session` for the collection job to be complete.

Job state is derived from date states rather than held only in Streamlit memory. Restarting the app or computer retains all completed work.

## Validation Rules

### Hard Failure

A source version fails when:

- the response is empty or cannot be parsed;
- mandatory columns are missing;
- the contained date differs from the requested date;
- a security code is duplicated for the same market and date;
- a code or market label is invalid;
- close, market capitalization, or listed shares is non-positive;
- volume or trading value is negative;
- any required numeric value is non-finite or unparseable;
- either KOSPI or KOSDAQ is entirely absent.

Zero volume and zero trading value are valid for a suspended or untraded security. A missing open is retained and later prevents execution at that open; the row itself is not discarded.

### Automatic Quarantine

A structurally valid source version is quarantined when:

- its security count falls outside 90% to 110% of the rolling median of the preceding 20 promoted sessions;
- total market capitalization changes by 20% or more from the preceding promoted session;
- the same date has a previously archived source with a different checksum;
- an unexpected schema or provider field type appears.

Threshold findings are safety alarms, not permission to delete inconvenient rows. Quarantine has no generic override button. Resolution requires a replacement official file or documented evidence selecting a particular raw version. The evidence and actor are stored in the audit record.

The first promoted dates, which lack sufficient history for rolling checks, still require all hard validations and later receive a retrospective range validation before the job can be declared complete.

## Network and Error Handling

The collector retries a date at most three times with increasing delays. It distinguishes connection failures, provider rejection, empty non-session responses, parser failures, and validation failures. Request pacing is configurable but always defaults to a conservative interval.

A failed date does not roll back earlier dates. Retrying a job selects only unresolved dates. Unexpected process termination leaves `.part` files untrusted; they are removed or replaced on the next attempt only after their resolved paths are verified to remain inside the raw archive root.

## Streamlit Workflow

The historical-data screen shows:

- requested start and end dates;
- total, promoted, non-session, quarantined, failed, and remaining counts;
- start, pause, and resume commands;
- exact failure and quarantine reasons by date;
- raw checksum, source type, fetched time, and validation findings for a selected date;
- separate acquisition-complete and official-backtest-ready indicators.

The first requested range defaults to the three-year performance interval plus at least 252 earlier trading sessions. The UI launches the worker and polls persisted status. It never holds the only copy of job progress.

Official CSV import is available for a selected unresolved date or date range. Imported files pass through the identical archive, normalization, validation, and promotion path.

## Survivorship and Time-Safety Rules

- No collection or normalization step may join against the current listing to decide which historical rows to retain.
- A code present on one historical day remains stored even if absent later.
- A security absent on a historical day cannot be inserted from a later listing.
- Names, market labels, and listed shares are stored as observed on each date rather than rewritten with current attributes.
- Missing rows are never manufactured by carrying another day's full-market snapshot forward.
- Acquisition success, canonical promotion, and official backtest eligibility are separate states.
- Historical classification and identifier linkage must use information effective on that date in the next increment.

## Persistence and Recovery

SQLite transactions protect promotion, and schema migrations are versioned. The logical model includes:

- collection jobs and requested date ranges;
- date-level attempts and state transitions;
- immutable raw versions and SHA-256 values;
- parser and normalizer versions;
- validation findings and resolution evidence;
- canonical daily market rows;
- rebuild and reconciliation records.

The raw archive is the recoverable source of truth. Deleting the derived SQLite daily tables and rebuilding them from the selected raw versions must reproduce the same row counts, values, and canonical fingerprints.

## Verification

Deterministic fixtures and integration tests must prove that:

- partial downloads are never accepted as raw versions;
- reruns are idempotent and do not duplicate rows;
- interrupted jobs resume only unresolved dates;
- a changed source for the same date is quarantined rather than overwritten;
- official CSV imports use the same pipeline as automatic downloads;
- promotion rolls back completely after an injected failure;
- leading zeroes in codes survive every stage;
- zero-volume and suspended securities are retained;
- a security present historically remains stored after it disappears later;
- no current-listing filter can remove a historical row;
- unresolved or unpromoted data cannot reach an engine-facing store;
- rebuilding from raw archives reproduces canonical fingerprints;
- malformed schemas, missing markets, duplicate codes, and non-finite values fail explicitly;
- Streamlit correctly starts, pauses, resumes, and reports a persisted job.

Five representative real KRX dates, including at least one low-volume session and one session adjacent to a holiday, are manually compared with the official display or download before production use.

## Completion Criteria

This increment is complete when:

- all new and existing automated tests pass;
- the five-date official-source comparison passes;
- interruption and resume have been exercised end to end;
- raw archive, manifest, and SQLite row counts and fingerprints reconcile;
- quarantined data cannot enter engine-facing storage;
- rebuilding normalized data from raw archives is deterministic;
- historical rows are retained independently of today's listing;
- the app clearly reports that official backtesting remains blocked by later data increments.

Completion does not certify the three-year production backtest. The next design increment must add historical security classification, listing and delisting life cycles, code changes, and delisting outcomes before daily records can become point-in-time universe snapshots.
