# Security Lifecycle Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable, rebuildable security-lifecycle foundation that reconstructs historical KOSPI and KOSDAQ listed membership from official evidence without current-listing survivorship bias.

**Architecture:** Add a separate lifecycle module family beside the existing historical daily-market collector. Official KRX adapters archive immutable source files, a pure builder derives identities/events/intervals, a transactional SQLite store promotes validated models, and a small repository interface exposes listed membership and readiness without equating listing with investability.

**Tech Stack:** Python 3.13, pandas 2.x, SQLite, requests, Streamlit 1.51+, unittest, SHA-256/gzip raw archives

**Spec:** `docs/superpowers/specs/2026-10-03-security-lifecycle-foundation-design.md`

## Global Constraints

- Keep current-listing data out of every historical retention and identity decision.
- Preserve issuer, security, and effective-dated identifiers as separate concepts.
- Store effective time and local knowledge time for every lifecycle event.
- Treat listing date as inclusive and delisting effective date as exclusive.
- Preserve last trading date separately; never infer it from delisting date.
- Keep listed membership distinct from tradability and strategy investability.
- Never infer delisting from a missing daily-market row.
- Keep unknown classification, unsupported lineage, and official-source conflicts blocked.
- Store changed official payloads as new immutable raw versions.
- Require deterministic rebuild fingerprints before lifecycle readiness can pass.
- Do not unlock official backtests in this increment.
- Add no new runtime dependency.

## Review Focus

- CSV encoding and Excel coercion: UTF-8 BOM and CP949 files must preserve six-character codes and standard codes without `.0` or scientific notation; Task 1 pins this.
- Date distinctions: a missing last-trading date must remain null rather than copying the delisting date; Task 2 pins this.
- Corrected official files: a second checksum for the same dataset and scope must remain a separate version and block promotion until selected; Tasks 1 and 4 pin this.
- Code reuse and overlap: non-overlapping code reuse is valid while simultaneous reuse or interval overlap is blocking; Task 2 pins this.
- Interrupted promotion: an exception during multi-table replacement must leave the previously promoted model and fingerprint intact; Task 3 pins this.

---

## File Structure

- Create `quantdesk/security_lifecycle.py`: canonical lifecycle types, normalization, identity/event/interval builder, validation, and fingerprints.
- Create `quantdesk/security_lifecycle_source.py`: KRX automatic and official-CSV source adapters plus dataset decoding.
- Create `quantdesk/security_lifecycle_store.py`: SQLite schema, immutable source manifests/rows, transactional model promotion, rebuild records, and read repository.
- Create `quantdesk/security_lifecycle_ingestion.py`: archive-to-parse-to-build-to-promote orchestration and rebuild workflow.
- Create `quantdesk/security_lifecycle_ui.py`: Streamlit lifecycle collection, diagnostics, timelines, and readiness section.
- Create `tests/security_lifecycle_fixtures.py`: deterministic official-source and lifecycle fixtures.
- Create `tests/test_security_lifecycle.py`: normalization, identity, events, intervals, conflicts, and fingerprints.
- Create `tests/test_security_lifecycle_source.py`: official source and encoding behavior.
- Create `tests/test_security_lifecycle_store.py`: schema, transactions, reconstruction, and repository reads.
- Create `tests/test_security_lifecycle_ingestion.py`: orchestration, source revisions, and idempotence.
- Create `tests/test_security_lifecycle_reconciliation.py`: daily-market mismatch and corporate-action tripwires.
- Create `tests/test_security_lifecycle_ui.py`: Streamlit behavior and readiness locks.
- Modify `app.py`: pass the lifecycle archive root into the historical-market page.
- Modify `quantdesk/historical_market_ui.py`: render the lifecycle section below daily acquisition.
- Modify `tests/historical_market_ui_fixture_app.py`: pass lifecycle raw-root configuration.
- Modify `tests/test_historical_market_ui.py`: retain daily collection behavior with the new section present.
- Modify `README.md`: explain lifecycle collection, evidence, and remaining locks.
- Modify `CHANGELOG.md`: record the lifecycle foundation increment.

### Task 1: Official Source Contract, Archive, and Parsing

**Files:**
- Create: `quantdesk/security_lifecycle_source.py`
- Create: `tests/security_lifecycle_fixtures.py`
- Create: `tests/test_security_lifecycle_source.py`

**Interfaces:**
- Consumes: bytes from an official KRX download or uploaded official CSV.
- Produces: `LifecycleSourceResult(dataset: str, scope_start: str | None, scope_end: str | None, content: bytes, metadata: dict)`, `LifecycleArchivedRaw(dataset: str, scope_start: str | None, scope_end: str | None, sha256: str, path: Path, fetched_at: str)`, `LifecycleRawArchive.store(result) -> LifecycleArchivedRaw`, `decode_lifecycle_source(result) -> pandas.DataFrame`, `OfficialLifecycleCsvSource.from_bytes(dataset, content, scope_start=None, scope_end=None) -> LifecycleSourceResult`, and `KrxLifecycleSource.fetch(dataset, scope_start=None, scope_end=None) -> LifecycleSourceResult`.

- [ ] **Step 1: Write failing source and archive tests**

Add tests named:

```python
def test_csv_decoding_preserves_codes_for_utf8_bom_and_cp949(): ...
def test_archive_is_atomic_idempotent_and_never_persists_credentials(): ...
def test_same_scope_with_changed_payload_creates_a_second_raw_version(): ...
def test_official_source_rejects_html_empty_and_partial_downloads(): ...
def test_krx_source_maps_each_supported_dataset_to_official_metadata(): ...
```

Assert support for exactly `security_master`, `new_listings`, `delistings`, and `identifier_changes`; codes normalize to six characters; secrets and query strings do not enter metadata; identical content reuses the same archived path; changed bytes produce a different checksum; unsupported datasets and HTML login/error pages fail explicitly.

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m unittest tests.test_security_lifecycle_source -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'quantdesk.security_lifecycle_source'`.

- [ ] **Step 3: Implement the source interfaces and immutable archive**

Use gzip with `mtime=0`, an fsynced `.part` file, `os.replace`, resolved-path containment, SHA-256 filenames, and UTC retrieval timestamps. Decode only UTF-8 BOM/UTF-8/CP949 CSV payloads; reject HTML and empty frames before returning. `KrxLifecycleSource` uses an injected `requests.Session`, timeout, and dataset query mapping so tests never call the network.

- [ ] **Step 4: Run source tests and relevant archive regressions**

Run: `python -m unittest tests.test_security_lifecycle_source tests.test_historical_market -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantdesk/security_lifecycle_source.py tests/security_lifecycle_fixtures.py tests/test_security_lifecycle_source.py
git commit -m "feat: archive official lifecycle sources"
```

### Task 2: Deterministic Identity, Event, and Interval Builder

**Files:**
- Create: `quantdesk/security_lifecycle.py`
- Modify: `tests/security_lifecycle_fixtures.py`
- Create: `tests/test_security_lifecycle.py`

**Interfaces:**
- Consumes: `normalize_lifecycle_rows(frame: pandas.DataFrame, dataset: str, raw_version_id: int, observed_at: str) -> pandas.DataFrame` output from all selected source datasets.
- Produces: `FindingLevel`, `LifecycleFinding`, `LifecycleModel`, `build_lifecycle_model(source_rows: pandas.DataFrame) -> LifecycleModel`, and `lifecycle_fingerprint(model: LifecycleModel) -> str`.

- [ ] **Step 1: Write failing normalization and timeline tests**

Add tests named:

```python
def test_normalization_requires_official_identity_and_effective_dates(): ...
def test_future_listing_is_absent_and_delisted_security_remains_before_delisting(): ...
def test_listing_is_inclusive_and_delisting_is_exclusive(): ...
def test_name_code_and_market_changes_create_non_overlapping_intervals(): ...
def test_common_and_preferred_shares_remain_distinct_under_one_official_issuer(): ...
def test_last_trading_date_is_not_inferred_from_delisting_date(): ...
def test_code_reuse_is_allowed_only_in_non_overlapping_intervals(): ...
def test_unknown_classification_and_unsupported_lineage_are_blocking(): ...
def test_fingerprint_is_order_independent_and_changes_with_canonical_content(): ...
```

Assert deterministic internal IDs derive from official identity fields, not row order; issuer links remain null when no official issuer reference exists; name changes do not create new securities; lineage does not merge histories; all canonical tables retain `raw_version_id` and `source_row_number` evidence.

- [ ] **Step 2: Run builder tests and verify RED**

Run: `python -m unittest tests.test_security_lifecycle -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'quantdesk.security_lifecycle'`.

- [ ] **Step 3: Implement canonical types and builder**

`LifecycleModel` contains DataFrames named `issuers`, `securities`, `identifiers`, `names`, `events`, `lineage`, `intervals`, and a tuple of `findings`. Generate internal IDs from a versioned SHA-256 namespace over official identity evidence. Sort canonical columns and rows before hashing. Emit structured warning, quarantine, or blocking findings rather than repairing conflicts.

- [ ] **Step 4: Run builder tests and existing point-in-time tests**

Run: `python -m unittest tests.test_security_lifecycle tests.test_backtest_data -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantdesk/security_lifecycle.py tests/security_lifecycle_fixtures.py tests/test_security_lifecycle.py
git commit -m "feat: build security lifecycle timelines"
```

### Task 3: Transactional SQLite Lifecycle Store and Read Interface

**Files:**
- Create: `quantdesk/security_lifecycle_store.py`
- Create: `tests/test_security_lifecycle_store.py`

**Interfaces:**
- Consumes: `LifecycleArchivedRaw`, normalized source rows, and `LifecycleModel` from Tasks 1-2.
- Produces: `SecurityLifecycleStore(path)`, `record_raw_version(archived, metadata, parser_version) -> int`, `save_source_rows(raw_version_id, rows) -> None`, `selected_source_rows() -> pandas.DataFrame`, `promote_model(model, source_selection) -> int`, `record_rebuild(...) -> int`, `listed_securities(as_of_date) -> pandas.DataFrame`, `lifecycle_timeline(security_id) -> dict`, and `readiness(start_date, end_date) -> dict`.

- [ ] **Step 1: Write failing schema, transaction, and repository tests**

Add tests named:

```python
def test_raw_manifest_and_normalized_rows_round_trip_with_evidence(): ...
def test_model_promotion_replaces_all_derived_tables_atomically(): ...
def test_failed_promotion_preserves_previous_model_and_fingerprint(): ...
def test_listed_securities_respects_inclusive_and_exclusive_dates(): ...
def test_timeline_returns_events_intervals_and_source_evidence(): ...
def test_readiness_blocks_unselected_revisions_and_blocking_findings(): ...
def test_rebuild_record_preserves_parser_rules_sources_and_fingerprint(): ...
```

Inject a failure after staging identities but before final promotion and assert that every previously promoted table and fingerprint is unchanged.

- [ ] **Step 2: Run store tests and verify RED**

Run: `python -m unittest tests.test_security_lifecycle_store -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'quantdesk.security_lifecycle_store'`.

- [ ] **Step 3: Implement schema version 1 and atomic promotion**

Create a separate `lifecycle_schema` version table in the existing market database. Use staging tables inside one SQLite transaction, foreign keys, unique effective-interval constraints, sorted UTF-8 audit JSON, and explicit source-selection rows. Keep the external read interface independent of physical table names.

- [ ] **Step 4: Run store and existing market-store tests**

Run: `python -m unittest tests.test_security_lifecycle_store tests.test_historical_market_store tests.test_market -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantdesk/security_lifecycle_store.py tests/test_security_lifecycle_store.py
git commit -m "feat: persist validated security lifecycles"
```

### Task 4: Ingestion, Source Revision Selection, and Deterministic Rebuild

**Files:**
- Create: `quantdesk/security_lifecycle_ingestion.py`
- Create: `tests/test_security_lifecycle_ingestion.py`

**Interfaces:**
- Consumes: source results and interfaces from Tasks 1-3.
- Produces: `SecurityLifecycleIngestion(store, archive)`, `ingest(result: LifecycleSourceResult) -> dict`, `select_raw_version(dataset: str, raw_version_id: int, evidence: str) -> None`, `build_and_promote() -> dict`, and `rebuild() -> dict`.

- [ ] **Step 1: Write failing ingestion and revision tests**

Add tests named:

```python
def test_ingest_archives_normalizes_and_registers_without_premature_promotion(): ...
def test_duplicate_ingest_is_idempotent(): ...
def test_first_valid_version_is_selected_without_manual_override(): ...
def test_changed_checksum_for_same_scope_requires_evidence_backed_selection(): ...
def test_build_refuses_missing_required_dataset_or_blocking_finding(): ...
def test_rebuild_from_selected_raw_versions_reproduces_fingerprint(): ...
def test_rebuild_mismatch_keeps_previous_model_and_records_failure(): ...
```

Assert that selection evidence cannot be blank, a selected version belongs to the requested dataset, and a parser/rule version change appears in the rebuild record.

- [ ] **Step 2: Run ingestion tests and verify RED**

Run: `python -m unittest tests.test_security_lifecycle_ingestion -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'quantdesk.security_lifecycle_ingestion'`.

- [ ] **Step 3: Implement the ingestion workflow**

Keep acquisition separate from promotion. Automatically select the first structurally valid version for a dataset and scope; a changed checksum clears automatic readiness and requires explicit source-row evidence before a version can be selected. `build_and_promote()` requires selected versions for all four datasets, rebuilds normalized rows from archived bytes, calls the pure builder, refuses quarantine/blocking findings, promotes once, then records a successful fingerprint reconciliation.

- [ ] **Step 4: Run ingestion and source/store suites**

Run: `python -m unittest tests.test_security_lifecycle_source tests.test_security_lifecycle tests.test_security_lifecycle_store tests.test_security_lifecycle_ingestion -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantdesk/security_lifecycle_ingestion.py tests/test_security_lifecycle_ingestion.py
git commit -m "feat: orchestrate lifecycle ingestion and rebuilds"
```

### Task 5: Daily-Market Reconciliation and Corporate-Action Tripwires

**Files:**
- Modify: `quantdesk/security_lifecycle.py`
- Modify: `quantdesk/security_lifecycle_store.py`
- Create: `tests/test_security_lifecycle_reconciliation.py`

**Interfaces:**
- Consumes: promoted lifecycle intervals and `historical_daily_market` rows.
- Produces: `reconcile_daily_market(store: SecurityLifecycleStore, start_date: str, end_date: str) -> tuple[LifecycleFinding, ...]` and lifecycle readiness fields `daily_coverage`, `unresolved_dates`, and `corporate_action_pending`.

- [ ] **Step 1: Write failing reconciliation tests**

Add tests named:

```python
def test_market_row_without_active_lifecycle_is_blocking(): ...
def test_active_security_missing_from_market_is_unresolved_not_delisted(): ...
def test_name_and_market_conflicts_preserve_both_source_values(): ...
def test_suspended_zero_volume_row_does_not_end_listing(): ...
def test_listed_share_change_of_twenty_percent_sets_corporate_action_pending(): ...
def test_close_ratio_outside_half_to_double_sets_corporate_action_pending(): ...
def test_reconciliation_never_populates_engine_facing_market_snapshots(): ...
```

Use exact tripwires of `abs(current_shares / previous_shares - 1) >= 0.20` and close ratio outside inclusive `[0.5, 2.0]`. These are blocking review signals, not automatic corporate-action classifications.

- [ ] **Step 2: Run reconciliation tests and verify RED**

Run: `python -m unittest tests.test_security_lifecycle_reconciliation -v`

Expected: FAIL because `reconcile_daily_market` is missing.

- [ ] **Step 3: Implement evidence-preserving reconciliation**

Read canonical daily rows by date, compare without modifying either source, persist findings with lifecycle and daily raw-version references, and update readiness only. Do not create `market_snapshots`, adjusted prices, tradability, or investability rows.

- [ ] **Step 4: Run lifecycle, historical-market, and backtest regression tests**

Run: `python -m unittest tests.test_security_lifecycle_reconciliation tests.test_historical_market_store tests.test_backtest_data -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quantdesk/security_lifecycle.py quantdesk/security_lifecycle_store.py tests/test_security_lifecycle_reconciliation.py
git commit -m "feat: reconcile lifecycle and daily market evidence"
```

### Task 6: Streamlit Lifecycle Audit Workflow

**Files:**
- Create: `quantdesk/security_lifecycle_ui.py`
- Modify: `app.py`
- Modify: `quantdesk/historical_market_ui.py`
- Modify: `tests/historical_market_ui_fixture_app.py`
- Create: `tests/test_security_lifecycle_ui.py`
- Modify: `tests/test_historical_market_ui.py`

**Interfaces:**
- Consumes: `SecurityLifecycleStore`, `SecurityLifecycleIngestion`, and source adapters.
- Produces: `render_security_lifecycle(db_path, raw_root) -> None`, called once by `render_historical_market(db_path, raw_root, lifecycle_raw_root=None)` below daily-market acquisition. Existing two-argument callers derive `Path(raw_root).parent / 'lifecycle'`; `app.py` passes `QUANTDESK_LIFECYCLE_RAW_ROOT` when configured.

- [ ] **Step 1: Write failing Streamlit tests**

Add tests named:

```python
def test_lifecycle_section_shows_four_source_states_and_separate_readiness_locks(): ...
def test_csv_import_uses_identical_ingestion_path_and_never_exposes_raw_editor(): ...
def test_changed_source_requires_version_selection_and_nonempty_evidence(): ...
def test_security_timeline_shows_identifiers_events_intervals_and_checksums(): ...
def test_rebuild_mismatch_remains_blocked_and_visible(): ...
def test_existing_daily_collection_controls_still_work(): ...
```

Assert distinct labels for lifecycle readiness, investability readiness, corporate-action readiness, and official-backtest readiness. The latter three remain locked even after lifecycle success.

- [ ] **Step 2: Run UI tests and verify RED**

Run: `python -m unittest tests.test_security_lifecycle_ui -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'quantdesk.security_lifecycle_ui'`.

- [ ] **Step 3: Implement the lifecycle section**

Use existing restrained Streamlit styling, Material icons, compact metrics, source tables, evidence inputs, timeline selection, and explicit blocking messages. Never persist credentials or offer a generic override/edit-canonical-data control.

- [ ] **Step 4: Run all historical-market UI tests**

Run: `python -m unittest tests.test_security_lifecycle_ui tests.test_historical_market_ui tests.test_app -v`

Expected: PASS with no Streamlit exceptions.

- [ ] **Step 5: Commit**

```bash
git add app.py quantdesk/security_lifecycle_ui.py quantdesk/historical_market_ui.py tests/historical_market_ui_fixture_app.py tests/test_security_lifecycle_ui.py tests/test_historical_market_ui.py
git commit -m "feat: add security lifecycle audit workflow"
```

### Task 7: End-to-End Fixture, Documentation, and Full Verification

**Files:**
- Modify: `tests/security_lifecycle_fixtures.py`
- Modify: `tests/test_security_lifecycle_ingestion.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: all lifecycle interfaces from Tasks 1-6.
- Produces: one deterministic end-to-end three-security lifecycle fixture and user-facing operating documentation.

- [ ] **Step 1: Write the failing end-to-end test**

Add `test_official_sources_rebuild_to_same_historical_membership_and_fingerprint()` covering a normal common share, a preferred share under the same issuer, and a later-delisted security. Assert listed membership before listing, during listing, and on the delisting effective date; source evidence for every interval; unchanged fingerprint after deleting and rebuilding derived tables; and all non-lifecycle readiness locks remain active.

- [ ] **Step 2: Run the end-to-end test and verify RED**

Run: `python -m unittest tests.test_security_lifecycle_ingestion.SecurityLifecycleEndToEndTests -v`

Expected: FAIL until the complete fixture and rebuild assertions are wired.

- [ ] **Step 3: Complete the fixture and document operation**

Document automatic collection, official CSV fallback, source-version selection, conflict interpretation, deterministic rebuild, listed-versus-investable distinction, and the remaining corporate-action lock. Add a changelog entry without claiming production backtest readiness.

- [ ] **Step 4: Run focused verification**

Run: `python -m unittest tests.test_security_lifecycle_source tests.test_security_lifecycle tests.test_security_lifecycle_store tests.test_security_lifecycle_ingestion tests.test_security_lifecycle_reconciliation tests.test_security_lifecycle_ui -v`

Expected: PASS.

- [ ] **Step 5: Run the complete suite and static checks**

Run: `python -m unittest discover -s tests -v`

Expected: all tests PASS.

Run: `python -m compileall -q app.py quantdesk tests`

Expected: exit code 0 with no output.

Run: `git diff --check`

Expected: exit code 0 with no output.

- [ ] **Step 6: Perform official-source manual checks**

Compare five archived official KRX examples with the official screens/downloads: one new listing, one delisting, one market transfer, one code/name change, and one preferred share. Record source URL/screen, retrieved time, raw checksum, expected event, derived interval, and reviewer result in the lifecycle audit store. Any mismatch blocks release.

- [ ] **Step 7: Commit**

```bash
git add tests/security_lifecycle_fixtures.py tests/test_security_lifecycle_ingestion.py README.md CHANGELOG.md
git commit -m "docs: explain security lifecycle evidence"
```

## Final Review Gate

- [ ] Confirm every promoted identity, event, and interval links to source rows and raw versions.
- [ ] Confirm no lifecycle module imports or calls the current-listing provider.
- [ ] Confirm listed membership is never labeled tradable or investable.
- [ ] Confirm changed checksums, rebuild mismatches, and unresolved classifications remain blocked.
- [ ] Confirm existing historical daily-market and fixture backtest behavior remains unchanged.
- [ ] Request a whole-branch code review focused on temporal correctness, source provenance, SQLite atomicity, and readiness-lock separation before opening the PR.
