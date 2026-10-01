# Historical Daily Market Data Acquisition Implementation Plan

**Goal:** Add a resumable, auditable, official-source-first collector for daily full-market KOSPI and KOSDAQ data without allowing incomplete data into the point-in-time backtest.

**Architecture:** Introduce a historical-market subsystem beside the current `MarketStore`. It archives immutable raw source versions, normalizes and validates one date at a time, persists acquisition state in the existing `market.db`, and promotes only validated rows into a new canonical daily table. A separate worker process performs long-running jobs while Streamlit reads persisted status. The existing engine-facing `market_snapshots` and `market_prices` tables remain untouched in this increment.

**Tech stack:** Python 3, pandas, requests, sqlite3, Streamlit, unittest.

**Spec:** `docs/superpowers/specs/2026-09-24-historical-market-data-acquisition-design.md`

## Fixed Decisions

- Initial coverage is three performance years plus at least 252 earlier trading sessions.
- Automatic acquisition uses the official Financial Services Commission stock-price OpenAPI backed by KRX data: `https://www.data.go.kr/data/15094808/openapi.do`.
- The OpenAPI service key is read from `QUANTDESK_DATA_GO_KR_KEY` or entered for the current Streamlit session. It is never stored in SQLite, raw metadata, logs, or Git.
- An official KRX or Public Data Portal CSV can fill unresolved dates, but it passes through the same archive, normalization, validation, and promotion path.
- Raw data is immutable and versioned. A new checksum for an existing date is quarantined rather than overwriting the selected version.
- Acquisition completion and official-backtest readiness remain separate.
- This increment never writes `market_snapshots`, `market_prices`, indices, financial statements, or corporate actions.
- Public-data license attribution and the noncommercial/no-redistribution limitation are shown in documentation. The app does not offer raw-source redistribution.

## Task 1: Define the Historical-Market Domain Contract

**Files:**

- Create: `quantdesk/historical_market.py`
- Create: `tests/test_historical_market.py`

### Step 1: Write failing value-object and state tests

Add tests for:

- `DailySourceResult` retaining requested date, source type, content bytes, and safe metadata;
- six-character codes preserving leading zeroes;
- the allowed date-state transitions:
  `pending -> downloading -> downloaded -> parsed -> validated -> promoted`;
- `failed`, `quarantined`, `calendar_unresolved`, and evidence-backed `non_session` terminal or recovery paths;
- rejection of impossible transitions such as `pending -> promoted`;
- API keys and URL query strings being excluded from serializable metadata.

Run:

```powershell
python -m unittest tests.test_historical_market -v
```

Expected: FAIL because the module and types do not exist.

### Step 2: Implement the smallest explicit contract

Create immutable dataclasses or equivalent small types for:

- source result;
- archived raw version;
- validation finding;
- date state and state transition;
- collection summary.

Keep provider-specific field names out of these public contracts. Use named exceptions for source, archive, parse, validation, and state errors so the UI does not inspect message text.

### Step 3: Re-run the focused test

Expected: PASS.

### Step 4: Commit

```powershell
git add quantdesk/historical_market.py tests/test_historical_market.py
git commit -m "feat: define historical market acquisition contract"
```

## Task 2: Add Immutable Raw Archiving

**Files:**

- Modify: `quantdesk/historical_market.py`
- Modify: `tests/test_historical_market.py`

### Step 1: Write failing archive tests

Use temporary directories to prove that:

- content is first written to a `.part` file in the destination directory;
- successful storage returns SHA-256 and a versioned `.gz` path;
- no `.part` file remains after success;
- an injected write failure never creates a final archive;
- identical bytes reuse the existing raw version;
- different bytes for the same date create a separate version;
- the resolved archive path cannot escape the configured raw root;
- decompression reproduces the original bytes exactly.

### Step 2: Implement `RawArchive`

Store files under:

```text
data/raw/krx/daily/YYYY/YYYY-MM-DD/<fetched-at>-<sha256>.<source>.gz
```

Write, flush, and replace atomically. Do not delete older versions. Make clocks injectable so tests have deterministic paths.

### Step 3: Run focused tests and `git diff --check`

Expected: PASS and no whitespace errors.

### Step 4: Commit

```powershell
git add quantdesk/historical_market.py tests/test_historical_market.py
git commit -m "feat: archive immutable daily market sources"
```

## Task 3: Build Strict Normalization and Validation

**Files:**

- Modify: `quantdesk/historical_market.py`
- Create: `tests/fixtures/historical_market/valid_day.json`
- Create: `tests/fixtures/historical_market/valid_day.csv`
- Modify: `tests/test_historical_market.py`

### Step 1: Add failing normalizer tests

Cover both official JSON rows and official CSV column names. Assert the canonical columns and types:

```text
trade_date, code, name, market, segment,
open, high, low, close, volume, amount, marcap, listed_shares
```

Prove that:

- KOSPI and KOSDAQ are retained while KONEX and non-stock operations are excluded;
- code `005930` remains a string;
- commas and blank opens normalize correctly;
- a missing open remains null and is never replaced by close;
- names and listed shares remain as observed for that date;
- rows are not joined to the current listing.

### Step 2: Add failing hard-validation tests

Reject empty data, wrong dates, missing columns, duplicate date/code rows, invalid markets or codes, non-positive close/marcap/listed shares, negative volume/amount, and non-finite required values. Retain zero-volume and zero-amount securities.

### Step 3: Add failing anomaly tests

Given prior promoted summaries, quarantine:

- row counts outside 90% to 110% of the prior 20-session median;
- total market capitalization changing by at least 20%;
- one market disappearing;
- schema drift;
- a changed checksum for an already selected date.

When fewer than 20 prior sessions exist, run hard checks now and mark the date for retrospective range validation.

### Step 4: Implement normalizer and validator

Return normalized data plus structured findings. Do not mutate caller frames. Sort canonical rows by market and code before fingerprinting.

### Step 5: Run focused tests

Expected: PASS.

### Step 6: Commit

```powershell
git add quantdesk/historical_market.py tests/test_historical_market.py tests/fixtures/historical_market
git commit -m "feat: validate daily full-market data"
```

## Task 4: Persist Jobs, Versions, Findings, and Canonical Days

**Files:**

- Create: `quantdesk/historical_market_store.py`
- Create: `tests/test_historical_market_store.py`

### Step 1: Write failing schema and round-trip tests

Create a temporary `market.db` and test logical tables for:

- schema version;
- collection jobs and requested dates;
- date attempts and state transitions;
- raw versions with checksum, relative path, source, fetched time, and parser version;
- validation findings and resolution evidence;
- canonical daily rows linked to the selected raw version;
- worker lease, heartbeat, pause request, and job summary.

Use foreign keys and uniqueness constraints for `(trade_date, code)` and raw checksum identity.

### Step 2: Write failing transaction tests

Inject an error halfway through promotion and assert that neither a partial canonical day nor a promoted state remains. Verify that promoting the same selected version twice is idempotent.

### Step 3: Write engine-firewall tests

Instantiate both `HistoricalMarketStore` and the existing `MarketStore` on the same SQLite path. Promote a canonical historical day and assert:

- the new canonical table contains all rows;
- `MarketStore.historical_snapshot(day)` is still empty;
- `MarketStore.prices(code)` is still unchanged.

### Step 4: Implement the store

Keep SQL and migration details inside `HistoricalMarketStore`. Enable SQLite foreign keys per connection. Return domain values or DataFrames, not raw cursor rows.

### Step 5: Run focused tests

```powershell
python -m unittest tests.test_historical_market_store -v
```

Expected: PASS.

### Step 6: Commit

```powershell
git add quantdesk/historical_market_store.py tests/test_historical_market_store.py
git commit -m "feat: persist historical market ingestion state"
```

## Task 5: Implement Official Sources Without Leaking Secrets

**Files:**

- Create: `quantdesk/historical_market_source.py`
- Create: `tests/test_historical_market_source.py`
- Modify: `requirements.txt` only if the existing requests constraint is insufficient

### Step 1: Write failing Public Data Portal client tests

Use a fake `requests.Session`; never call the network in automated tests. Cover:

- `basDt=YYYYMMDD`, `resultType=json`, pagination, and bounded page size;
- reading all pages until `totalCount` is satisfied;
- preserving each raw page in an archived response envelope;
- accepting only rows whose `basDt` matches the request;
- mapping official fields such as `srtnCd`, `mrktCtg`, `mkp`, `clpr`, `trqu`, `trPrc`, `lstgStCnt`, and `mrktTotAmt`;
- empty successful responses returning a non-session candidate, not a promoted day;
- official error codes becoming typed authentication, quota, rate-limit, timeout, or provider errors;
- the service key never appearing in exceptions, `repr`, metadata, or logs.

### Step 2: Write failing official-CSV tests

Accept uploaded bytes plus the requested date. Reject files containing another date or no recognizable official columns. Return the same `DailySourceResult` contract as the API client.

### Step 3: Implement `DataGoKrDailySource` and `OfficialCsvSource`

Use the documented endpoint:

```text
https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo
```

Set connect/read timeouts, a descriptive user agent, conservative request spacing, and no automatic retry inside the client. Job orchestration owns retries so attempts remain auditable.

### Step 4: Run focused tests

Expected: PASS.

### Step 5: Commit

```powershell
git add quantdesk/historical_market_source.py tests/test_historical_market_source.py requirements.txt
git commit -m "feat: add official daily market sources"
```

## Task 6: Orchestrate Resumable Collection and Promotion

**Files:**

- Modify: `quantdesk/historical_market.py`
- Modify: `quantdesk/historical_market_store.py`
- Create: `tests/test_historical_market_ingestion.py`

### Step 1: Write failing end-to-end fixture tests

With fake source responses and a temporary raw root/database, verify:

- weekends are excluded when creating a job;
- completed and evidence-backed non-session dates are skipped on resume;
- failed and unresolved dates are retried at most three times;
- one failed date does not undo previous promoted dates;
- pause is observed between dates and resume continues correctly;
- a second worker cannot acquire an active lease;
- a stale lease can be reclaimed after its documented timeout;
- changed checksums and anomaly findings enter quarantine;
- a valid date archives, parses, validates, and promotes in order;
- a weekday with an empty response ends as `calendar_unresolved` after retries;
- no raw or canonical result contains the service key;
- rebuilding canonical rows from selected raw versions reproduces the same fingerprint.

### Step 2: Implement `HistoricalIngestionService`

Keep one public operation per use case:

- create a date-range job;
- process the next unresolved date;
- request pause or resume;
- import official CSV bytes;
- rebuild a selected raw version;
- summarize and reconcile a job.

Persist every state transition. Retry delays are injected in tests and use increasing waits in production.

### Step 3: Add retrospective range validation

Before marking a job complete, rerun rolling anomaly checks across the complete promoted range. A newly discovered anomaly changes the affected date to quarantined and the job to incomplete.

### Step 4: Run focused tests

Expected: PASS.

### Step 5: Commit

```powershell
git add quantdesk/historical_market.py quantdesk/historical_market_store.py tests/test_historical_market_ingestion.py
git commit -m "feat: orchestrate resumable market ingestion"
```

## Task 7: Add the Background Worker Entry Point

**Files:**

- Create: `quantdesk/historical_market_worker.py`
- Create: `tests/test_historical_market_worker.py`

### Step 1: Write failing worker tests

Test `main(argv, environ, service_factory)` directly rather than spawning real processes. Verify:

- required job/database/raw-root arguments;
- key lookup from `QUANTDESK_DATA_GO_KR_KEY` without echoing it;
- clear nonzero exit codes for missing key, active lease, and unexpected failure;
- normal exit after completion or cooperative pause;
- heartbeat updates while processing;
- no shell command construction.

### Step 2: Implement the worker

Run with:

```powershell
python -m quantdesk.historical_market_worker --job-id <id> --db <path> --raw-root <path>
```

The worker processes one date at a time, checks pause state between dates, and releases its lease in `finally`. Keep subprocess launching outside this module so tests do not require Windows process control.

### Step 3: Run focused tests

Expected: PASS.

### Step 4: Commit

```powershell
git add quantdesk/historical_market_worker.py tests/test_historical_market_worker.py
git commit -m "feat: add historical market worker"
```

## Task 8: Add the Streamlit Historical-Data Workflow

**Files:**

- Create: `quantdesk/historical_market_ui.py`
- Create: `tests/historical_market_ui_fixture_app.py`
- Create: `tests/test_historical_market_ui.py`
- Modify: `app.py`
- Modify: `tests/test_app.py`

### Step 1: Write failing UI tests

Use `streamlit.testing.v1.AppTest` and injected launch functions. Cover:

- a new `과거 시장 데이터` sidebar page;
- default date range covering three years plus a 252-session buffer estimate;
- service-key input rendered as a password and not persisted;
- start disabled for invalid dates or a missing key when API mode is selected;
- start creating a persisted job and invoking a safe argument-list launcher;
- pause and resume changing persisted state;
- promoted, non-session, quarantined, failed, and remaining metrics;
- exact date-level findings and source checksum display;
- CSV upload using the same ingestion service;
- acquisition complete remaining distinct from official-backtest ready;
- the existing Backtest button remaining disabled after a historical daily job completes.

### Step 2: Implement the page

Use a compact operational layout rather than dashboard cards. Keep controls stable while progress changes. Poll the database on rerun and never store job truth only in `st.session_state`.

Launch the worker with `subprocess.Popen` using an argument list and `shell=False`. Inject the launcher for tests. On Windows use hidden-process flags; on other platforms use a detached session where available. Store only job identifiers and process metadata, never the key. Pass a session-entered key to the child through a minimal copied environment and do not write that environment to logs.

### Step 3: Run UI tests

```powershell
python -m unittest tests.test_historical_market_ui tests.test_app -v
```

Expected: PASS.

### Step 4: Commit

```powershell
git add app.py quantdesk/historical_market_ui.py tests/historical_market_ui_fixture_app.py tests/test_historical_market_ui.py tests/test_app.py
git commit -m "feat: add historical market collection workflow"
```

## Task 9: Document Setup, Attribution, and Recovery

**Files:**

- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Create: `.env.example`
- Modify: `.gitignore` only if new generated artifacts are not already covered

### Step 1: Add documentation assertions if practical

Add a small test or existing app assertion proving no secret value is rendered or persisted. Do not create brittle tests for prose.

### Step 2: Document the operator workflow

Cover:

- applying for the free `금융위원회_주식시세정보` service on the Public Data Portal;
- setting `QUANTDESK_DATA_GO_KR_KEY` or entering it for one session;
- starting, pausing, and resuming the job;
- importing an official CSV for unresolved dates;
- interpreting failed, quarantined, non-session, acquisition-complete, and backtest-ready states;
- backing up `data/raw/krx/daily` and `data/market.db`;
- rebuilding normalized data from raw archives;
- source attribution, noncommercial use, and prohibition on raw redistribution;
- the deliberate backtest lock until later lifecycle and corporate-action increments.

Never place a real key in `.env.example`.

### Step 3: Commit

```powershell
git add README.md CHANGELOG.md .env.example .gitignore
git commit -m "docs: explain historical market collection"
```

## Task 10: Full Verification and Manual Smoke Test

### Step 1: Run the complete automated suite

```powershell
python -m unittest discover -s tests -v
```

Expected: all existing and new tests PASS.

### Step 2: Run static repository checks

```powershell
python -m compileall app.py quantdesk tests
git diff --check
git status --short
```

Expected: compilation succeeds, no whitespace errors, and only intended changes remain.

### Step 3: Run deterministic reconstruction

Using fixture archives, delete only the temporary derived canonical tables, rebuild through the public service, and compare row counts and fingerprints with the original fixture run.

Expected: exact match.

### Step 4: Run the local Streamlit smoke test

```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8507
```

Verify:

- every existing page loads;
- `과거 시장 데이터` creates and monitors a fixture or short authorized job;
- pause and resume survive a Streamlit rerun;
- no secret appears in the UI, terminal output, SQLite, or raw metadata;
- historical canonical data does not unlock the Backtest page.

### Step 5: Perform the five-date official comparison

Choose five authorized KRX-linked dates, including a low-volume day and a session adjacent to a holiday. Compare row count and representative OHLC, volume, amount, market cap, and listed-share values against the official portal display or download. Record only dates, checksums, comparison status, and discrepancies; do not commit licensed raw data.

Expected: all five comparisons PASS or the affected dates remain quarantined.

### Step 6: Request code review before integration

Review specifically for:

- survivorship and look-ahead leakage;
- raw-file path safety and atomicity;
- secret leakage;
- SQLite transaction and lease correctness;
- worker duplication and resume behavior;
- accidental writes to engine-facing tables;
- licensing and redistribution exposure;
- missing or overly permissive validation.

Resolve findings, rerun the full suite, and only then prepare the pull request.

## Subsequent Increment

After this plan is integrated, design and implement historical security classification, listing/delisting life cycles, code changes, and delisting outcomes. Only that enriched and validated layer may populate point-in-time universe snapshots for the existing backtest engine.
