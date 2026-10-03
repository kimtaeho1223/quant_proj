# Point-in-Time Investability and Trading Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable point-in-time layer that reconstructs whether each historical KOSPI or KOSDAQ security was policy eligible and executable without present-day status leakage.

**Architecture:** Archive official KRX status snapshots as immutable evidence, normalize them into bitemporal status assertions, and build deterministic effective intervals linked to lifecycle `security_id`. Persist promoted models transactionally in SQLite, expose storage-independent point-in-time decisions, reconcile against lifecycle and daily-market evidence, and keep corporate-action and official-backtest readiness locked.

**Tech Stack:** Python 3.13, pandas, SQLite, Streamlit, standard-library `unittest`, existing Quant Desk archive/store patterns

**Spec:** `docs/superpowers/specs/2026-10-03-investability-trading-status-design.md`

## Global Constraints

- Permit only KOSPI and KOSDAQ common shares under policy version `investability-v1`.
- Exclude preferred shares, SPACs, REITs, ETFs, ETNs, and unknown classifications.
- Block management designation, trading suspension, and liquidation trading.
- Preserve `effective_from`, half-open `effective_to`, `known_at`, and `observed_at` separately.
- Date-only evidence without a proven publication time is knowable no earlier than the next official market session.
- Missing, conflicting, unselected, future, or ambiguous evidence returns `unknown`; never infer `eligible`.
- Do not infer official status from zero volume, missing prices, or price moves.
- Keep the 252-session history rule outside the investability domain.
- Never persist browser cookies, login sessions, API secrets, or user-supplied archive paths.
- Raw versions are append-only and addressed by SHA-256.
- Promotion and rebuild metadata writes are transactional.
- This increment may clear only investability readiness. Corporate-action and official-backtest readiness remain locked.
- Do not change the existing fixture backtest's ranking, execution, or performance semantics.

## Review Focus

- A status snapshot contains a short code reused by another lifecycle security: quarantine identity ambiguity rather than selecting by current code. Covered in Task 2.
- A release notice is published after the signal close but has the same calendar effective date: the earlier signal must still see the blocking status. Covered in Task 2.
- A selected raw version is replaced by a corrected version with the same coverage key: retain both versions and lock readiness until a reasoned selection is recorded. Covered in Task 4.
- A signal-eligible security becomes suspended before the next-session open: reject the order while preserving the signal audit. Covered in Task 6.
- A daily snapshot is absent between two equal-looking status days: return `unknown` for the gap instead of forward-filling. Covered in Tasks 2 and 5.

---

## File Structure

- Create `quantdesk/investability_source.py`: official-source contracts, immutable archive, CSV decoding, and normalized source rows.
- Create `quantdesk/investability.py`: pure status intervals, policy decisions, fingerprints, and reconciliation findings.
- Create `quantdesk/investability_store.py`: versioned SQLite schema, transactional promotion, read interface, readiness, rebuilds, and reviews.
- Create `quantdesk/investability_ingestion.py`: collection, fallback ingestion, source selection, rebuild, and reconciliation orchestration.
- Create `quantdesk/investability_ui.py`: Streamlit audit workflow and status inspection.
- Modify `quantdesk/historical_market_ui.py`: render the investability workflow after lifecycle audit.
- Modify `quantdesk/backtest_data.py`: consume point-in-time universe and execution decisions through an adapter without unlocking official runs.
- Modify `quantdesk/backtest_ui.py`: display investability readiness separately from later locks.
- Create `tests/investability_fixtures.py`: deterministic source, lifecycle, and market fixtures.
- Create focused `tests/test_investability_*.py` modules matching each production module.
- Modify `tests/test_backtest_data.py`, `tests/test_backtest_ui.py`, and `tests/historical_market_ui_fixture_app.py` for integration contracts.
- Modify `README.md` and `CHANGELOG.md` after behavior is verified.

### Task 1: Official Source Contract, Archive, and Parser

**Files:**
- Create: `quantdesk/investability_source.py`
- Create: `tests/investability_fixtures.py`
- Create: `tests/test_investability_source.py`

**Interfaces:**
- Consumes: an HTTP getter compatible with `requests.get`, official CSV bytes, internally generated archive root, dataset name, requested date, and retrieval timestamp.
- Produces: `InvestabilityRawArtifact`, `archive_investability_payload(...)`, `decode_investability_csv(...)`, `normalize_investability_rows(...)`, and `fetch_investability_snapshot(...)`.

- [ ] **Step 1: Write failing archive and parser tests**

Add tests named:

- `test_archive_is_checksum_addressed_and_does_not_overwrite_changed_bytes`
- `test_archive_rejects_path_escape_and_oversized_payload`
- `test_parser_normalizes_official_korean_and_krx_column_aliases`
- `test_parser_preserves_source_row_and_observed_at`
- `test_parser_rejects_empty_or_wrong_date_snapshot`
- `test_parser_treats_csv_formula_cells_as_plain_text`
- `test_fetch_surfaces_login_and_authentication_failures_without_secrets`

Assert six-digit short codes, canonical booleans for all three blocking flags, requested-date equality, raw row numbers, SHA-256 stability, and redacted error text.

- [ ] **Step 2: Run the source tests and verify failure**

Run: `python -m unittest tests.test_investability_source -v`

Expected: FAIL because `quantdesk.investability_source` does not exist.

- [ ] **Step 3: Implement source types and immutable archive**

Implement:

```python
@dataclass(frozen=True)
class InvestabilityRawArtifact:
    dataset: str
    requested_date: str
    sha256: str
    path: Path
    observed_at: str
    source_url: str

def archive_investability_payload(root: Path, dataset: str, requested_date: str,
                                  payload: bytes, observed_at: str,
                                  source_url: str) -> InvestabilityRawArtifact: ...
```

Use internally generated paths below `data/raw/krx/investability`, atomic replacement, bounded payload size, and the lifecycle archive's URL-redaction rules.

- [ ] **Step 4: Implement strict CSV decoding, normalization, and fetch errors**

Implement:

```python
def decode_investability_csv(payload: bytes) -> pd.DataFrame: ...
def normalize_investability_rows(frame: pd.DataFrame, dataset: str,
                                 requested_date: str, raw_version_id: int,
                                 observed_at: str) -> pd.DataFrame: ...
def fetch_investability_snapshot(requested_date: str, http_get,
                                 service_key: str | None = None) -> bytes: ...
```

Canonical rows contain `dataset`, `requested_date`, `raw_version_id`,
`source_row_number`, `observed_at`, `standard_code`, `short_code`, `name`,
`market`, `security_kind`, `management_designation`, `trading_suspension`,
`liquidation_trading`, `effective_date`, and `published_at`.

- [ ] **Step 5: Run the source tests**

Run: `python -m unittest tests.test_investability_source -v`

Expected: PASS.

- [ ] **Step 6: Commit the source contract**

```bash
git add quantdesk/investability_source.py tests/investability_fixtures.py tests/test_investability_source.py
git commit -m "feat: archive official investability evidence"
```

### Task 2: Bitemporal Status Intervals and Policy Decisions

**Files:**
- Create: `quantdesk/investability.py`
- Create: `tests/test_investability.py`
- Modify: `tests/investability_fixtures.py`

**Interfaces:**
- Consumes: normalized Task 1 rows, a lifecycle model/read interface, official session dates, and an `InvestabilityPolicy`.
- Produces: `InvestabilityPolicy`, `StatusFinding`, `StatusModel`, `InvestabilityDecision`, `build_status_model(...)`, `decision_at(...)`, and `investability_fingerprint(...)`.

- [ ] **Step 1: Write failing policy and time-model tests**

Add tests named:

- `test_v1_policy_allows_only_kospi_kosdaq_common_shares`
- `test_each_blocking_flag_produces_ineligible_with_evidence`
- `test_missing_day_between_snapshots_is_unknown_not_forward_filled`
- `test_release_known_after_signal_close_does_not_rewrite_signal_decision`
- `test_date_only_evidence_becomes_known_on_next_session`
- `test_reused_short_code_requires_unique_lifecycle_identity`
- `test_overlapping_contradictory_assertions_are_blocking`
- `test_not_listed_is_distinct_from_unknown`
- `test_fingerprint_is_stable_under_input_row_reordering`

Use explicit timestamps around a 15:30 KST signal close and assert reason codes and evidence identifiers, not only booleans.

- [ ] **Step 2: Run the model tests and verify failure**

Run: `python -m unittest tests.test_investability -v`

Expected: FAIL because the model interfaces do not exist.

- [ ] **Step 3: Implement immutable domain types and policy**

Implement:

```python
@dataclass(frozen=True)
class InvestabilityPolicy:
    policy_id: str
    version: str
    allowed_markets: tuple[str, ...]
    allowed_security_kinds: tuple[str, ...]
    blocking_flags: tuple[str, ...]

@dataclass(frozen=True)
class InvestabilityDecision:
    security_id: str
    as_of_timestamp: str
    state: str
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    policy_fingerprint: str
    model_fingerprint: str
```

Define canonical `INVESTABILITY_V1` and explicit `eligible`, `ineligible`,
`unknown`, and `not_listed` states.

- [ ] **Step 4: Implement interval construction and point-in-time decisions**

Implement:

```python
def build_status_model(rows: pd.DataFrame, lifecycle_reader,
                       sessions: pd.DataFrame) -> StatusModel: ...
def decision_at(model: StatusModel, security_id: str, as_of_timestamp: str,
                policy: InvestabilityPolicy) -> InvestabilityDecision: ...
def investability_fingerprint(model: StatusModel,
                              policy: InvestabilityPolicy) -> str: ...
```

Use half-open effective intervals, separate knowledge time, exact lifecycle
identity, and no implicit coverage across a missing requested date.

- [ ] **Step 5: Run model and lifecycle regression tests**

Run: `python -m unittest tests.test_investability tests.test_security_lifecycle -v`

Expected: PASS.

- [ ] **Step 6: Commit the pure model**

```bash
git add quantdesk/investability.py tests/investability_fixtures.py tests/test_investability.py
git commit -m "feat: model point-in-time investability decisions"
```

### Task 3: Transactional SQLite Store and Read Interface

**Files:**
- Create: `quantdesk/investability_store.py`
- Create: `tests/test_investability_store.py`

**Interfaces:**
- Consumes: raw artifact metadata, normalized rows, `StatusModel`, selected raw versions, policy, findings, and date range.
- Produces: `InvestabilityStore.initialize()`, `save_raw_version(...)`, `promote(...)`, `decision(...)`, `universe(...)`, `execution_status(...)`, `readiness(...)`, `record_rebuild(...)`, and `record_manual_review(...)`.

- [ ] **Step 1: Write failing schema, transaction, and query tests**

Add tests named:

- `test_initialize_is_idempotent_and_records_schema_version`
- `test_raw_versions_are_append_only_and_checksum_unique`
- `test_failed_promotion_rolls_back_all_model_tables`
- `test_decision_and_universe_return_policy_and_evidence_metadata`
- `test_execution_status_is_independent_from_signal_decision`
- `test_readiness_is_scoped_to_every_requested_date_and_policy`
- `test_manual_review_cannot_mutate_raw_facts`
- `test_rebuild_result_records_expected_and_actual_fingerprint`

- [ ] **Step 2: Run store tests and verify failure**

Run: `python -m unittest tests.test_investability_store -v`

Expected: FAIL because the store does not exist.

- [ ] **Step 3: Implement versioned schema and raw-version writes**

Create `investability_schema` plus logical tables for raw versions, source rows,
source selections, policies, model metadata, assertions, intervals, findings,
reconciliation runs, rebuilds, and manual reviews. Use foreign keys, unique
coverage keys, parameterized SQL, and UTC ISO timestamps.

- [ ] **Step 4: Implement transactional promotion and read methods**

Implement:

```python
class InvestabilityStore:
    def promote(self, model: StatusModel, policy: InvestabilityPolicy,
                source_selection: dict[str, int]) -> str: ...
    def decision(self, security_id: str, as_of_timestamp: str,
                 policy_id: str) -> dict: ...
    def universe(self, as_of_timestamp: str, policy_id: str) -> pd.DataFrame: ...
    def execution_status(self, security_id: str, session_date: str,
                         decision_timestamp: str, policy_id: str) -> dict: ...
    def readiness(self, start_date: str, end_date: str,
                  policy_id: str) -> dict: ...
```

Keep callers independent of table names.

- [ ] **Step 5: Run store tests**

Run: `python -m unittest tests.test_investability_store -v`

Expected: PASS.

- [ ] **Step 6: Commit the storage layer**

```bash
git add quantdesk/investability_store.py tests/test_investability_store.py
git commit -m "feat: persist validated investability status"
```

### Task 4: Ingestion, Source Revision Selection, and Deterministic Rebuild

**Files:**
- Create: `quantdesk/investability_ingestion.py`
- Create: `tests/test_investability_ingestion.py`
- Modify: `quantdesk/investability_source.py`
- Modify: `quantdesk/investability_store.py`

**Interfaces:**
- Consumes: Task 1 source functions, Task 2 model builder, Task 3 store, lifecycle reader, session calendar, and official CSV fallback.
- Produces: `collect_investability_date(...)`, `ingest_investability_csv(...)`, `select_investability_source(...)`, and `rebuild_investability_model(...)`.

- [ ] **Step 1: Write failing orchestration and revision tests**

Add tests named:

- `test_collection_archives_before_parsing_and_preserves_valid_model_on_failure`
- `test_official_csv_fallback_uses_same_validation_pipeline`
- `test_changed_same_coverage_payload_creates_unselected_revision_lock`
- `test_selection_requires_nonempty_official_reason_for_revision`
- `test_rebuild_from_selected_raw_versions_matches_promoted_fingerprint`
- `test_rebuild_mismatch_keeps_readiness_locked`
- `test_authentication_failure_returns_actionable_fallback_status`

- [ ] **Step 2: Run ingestion tests and verify failure**

Run: `python -m unittest tests.test_investability_ingestion -v`

Expected: FAIL because orchestration functions do not exist.

- [ ] **Step 3: Implement collection and fallback ingestion**

Implement:

```python
def collect_investability_date(store, archive_root: Path, requested_date: str,
                               lifecycle_reader, sessions, http_get,
                               service_key: str | None = None) -> dict: ...
def ingest_investability_csv(store, archive_root: Path, requested_date: str,
                             payload: bytes, lifecycle_reader,
                             sessions) -> dict: ...
```

Archive before parse, use one validation pipeline, and preserve the last valid
promoted model on every failure.

- [ ] **Step 4: Implement source selection and deterministic rebuild**

Implement:

```python
def select_investability_source(store, coverage_key: str, raw_version_id: int,
                                reason: str, lifecycle_reader,
                                sessions) -> dict: ...
def rebuild_investability_model(store, lifecycle_reader,
                                sessions) -> dict: ...
```

Require a reason when selected bytes differ from an earlier version and compare
canonical fingerprints after rebuilding from archive bytes.

- [ ] **Step 5: Run ingestion and store tests**

Run: `python -m unittest tests.test_investability_ingestion tests.test_investability_store -v`

Expected: PASS.

- [ ] **Step 6: Commit orchestration**

```bash
git add quantdesk/investability_ingestion.py quantdesk/investability_source.py quantdesk/investability_store.py tests/test_investability_ingestion.py
git commit -m "feat: orchestrate investability ingestion and rebuilds"
```

### Task 5: Lifecycle and Daily-Market Reconciliation

**Files:**
- Modify: `quantdesk/investability.py`
- Modify: `quantdesk/investability_ingestion.py`
- Modify: `quantdesk/investability_store.py`
- Create: `tests/test_investability_reconciliation.py`

**Interfaces:**
- Consumes: promoted status model, lifecycle intervals, canonical daily-market rows, selected date range, and policy.
- Produces: `reconcile_investability(...)` and persisted date/security findings used by readiness.

- [ ] **Step 1: Write failing reconciliation tests**

Add tests named:

- `test_status_outside_listing_interval_is_blocking`
- `test_listed_security_without_selected_daily_status_is_unknown`
- `test_market_row_without_lifecycle_identity_is_quarantined`
- `test_zero_volume_does_not_create_suspension`
- `test_price_gap_is_warning_only_without_official_status`
- `test_absent_middle_snapshot_remains_unknown_even_when_neighbors_match`
- `test_reconciliation_is_date_scoped_and_does_not_hide_failed_days`

- [ ] **Step 2: Run reconciliation tests and verify failure**

Run: `python -m unittest tests.test_investability_reconciliation -v`

Expected: FAIL because reconciliation is not implemented.

- [ ] **Step 3: Implement pure reconciliation**

Implement:

```python
def reconcile_investability(model: StatusModel, lifecycle_reader,
                            daily_market: pd.DataFrame, start_date: str,
                            end_date: str,
                            policy: InvestabilityPolicy) -> tuple[pd.DataFrame, tuple[StatusFinding, ...]]: ...
```

Use price and volume only for warnings; only official status assertions control
eligibility.

- [ ] **Step 4: Persist reconciliation and include it in readiness**

Store each run, finding, source model fingerprint, lifecycle fingerprint, market
raw version, date range, and policy fingerprint. `readiness` must list every
unknown or blocked date in the requested range.

- [ ] **Step 5: Run reconciliation and historical-market regressions**

Run: `python -m unittest tests.test_investability_reconciliation tests.test_historical_market_store tests.test_security_lifecycle_reconciliation -v`

Expected: PASS.

- [ ] **Step 6: Commit reconciliation**

```bash
git add quantdesk/investability.py quantdesk/investability_ingestion.py quantdesk/investability_store.py tests/test_investability_reconciliation.py
git commit -m "feat: reconcile investability with market evidence"
```

### Task 6: Point-in-Time Backtest Contract

**Files:**
- Modify: `quantdesk/backtest_data.py`
- Modify: `quantdesk/backtest_ui.py`
- Modify: `tests/test_backtest_data.py`
- Modify: `tests/test_backtest_ui.py`

**Interfaces:**
- Consumes: `InvestabilityStore.universe(...)`, `execution_status(...)`, and `readiness(...)` from Task 3.
- Produces: signal-date investability filtering, execution-day rejection records, and separate investability/corporate-action/official readiness labels without unlocking official runs.

- [ ] **Step 1: Write failing signal/execution integration tests**

Add tests named:

- `test_signal_universe_uses_investability_decision_at_signal_timestamp`
- `test_252_session_rule_remains_strategy_readiness_not_investability`
- `test_execution_day_suspension_rejects_order_and_preserves_signal_audit`
- `test_existing_suspended_holding_carries_last_close_for_valuation_only`
- `test_unknown_investability_blocks_official_result`
- `test_investability_ready_does_not_clear_corporate_action_or_official_locks`
- `test_fixture_backtest_result_is_unchanged_without_official_adapter`

- [ ] **Step 2: Run integration tests and verify failure**

Run: `python -m unittest tests.test_backtest_data tests.test_backtest_ui -v`

Expected: FAIL on missing adapter behavior while existing fixture tests remain passing.

- [ ] **Step 3: Add an optional point-in-time universe adapter**

Add constructor/configuration inputs to the existing data layer without changing
the default fixture path. Filter signal listings by `eligible`, retain decision
metadata in weekly audit, and keep the existing 252-session ranking validation.

- [ ] **Step 4: Add execution recheck and rejection audit**

Before each generated fill, query execution status for the order session. Reject
non-eligible execution with `security_id`, status, reason codes, evidence IDs,
signal timestamp, and execution timestamp. Do not substitute close prices.

- [ ] **Step 5: Display readiness locks separately**

Show lifecycle, investability, corporate-action, and official-backtest readiness
as distinct labels. Even a fully ready investability range leaves the latter two
locked in this increment.

- [ ] **Step 6: Run all backtest tests**

Run: `python -m unittest tests.test_backtest_data tests.test_backtest_execution tests.test_backtest tests.test_backtest_ui -v`

Expected: PASS with unchanged fixture outputs plus the new rejection audit.

- [ ] **Step 7: Commit the backtest contract**

```bash
git add quantdesk/backtest_data.py quantdesk/backtest_ui.py tests/test_backtest_data.py tests/test_backtest_ui.py
git commit -m "feat: enforce point-in-time investability in backtests"
```

### Task 7: Streamlit Audit Workflow, Documentation, and Final Verification

**Files:**
- Create: `quantdesk/investability_ui.py`
- Create: `tests/test_investability_ui.py`
- Modify: `quantdesk/historical_market_ui.py`
- Modify: `tests/test_historical_market_ui.py`
- Modify: `tests/historical_market_ui_fixture_app.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: all ingestion, store, rebuild, reconciliation, and readiness interfaces from Tasks 1-6.
- Produces: a user-facing audit workflow, source fallback, status inspection, final documentation, and release evidence.

- [ ] **Step 1: Write failing UI state tests**

Add tests named:

- `test_ui_shows_collection_and_official_csv_fallback_without_cookie_input`
- `test_ui_requires_reason_before_selecting_changed_revision`
- `test_ui_shows_unknown_dates_and_evidence_backed_exclusions`
- `test_ui_distinguishes_all_four_readiness_locks`
- `test_ui_rebuild_mismatch_is_blocking`
- `test_uploaded_filename_cannot_control_archive_path`
- `test_ui_does_not_claim_official_backtest_readiness`

- [ ] **Step 2: Run UI tests and verify failure**

Run: `python -m unittest tests.test_investability_ui tests.test_historical_market_ui -v`

Expected: FAIL because the UI module is absent.

- [ ] **Step 3: Implement `render_investability_audit(...)`**

Implement:

```python
def render_investability_audit(store, archive_root: Path,
                               lifecycle_reader, sessions,
                               daily_market_reader) -> None: ...
```

Provide collection, official CSV fallback, source selection with reason, rebuild,
reconciliation, date coverage, finding tables, per-security decision lookup, and
four separate readiness labels. Do not expose manual editing of raw facts.

- [ ] **Step 4: Wire the workflow into historical market UI**

Render after lifecycle audit using the existing market database and raw-data
root. Keep expensive work button-driven and persist progress in SQLite rather
than Streamlit session state alone.

- [ ] **Step 5: Update user documentation and changelog**

Document source acquisition, official CSV fallback, policy `investability-v1`,
knowledge-time rules, signal/execution separation, unknown-state behavior,
backup requirements, and remaining corporate-action lock. Do not claim an
official backtest is ready.

- [ ] **Step 6: Run focused and complete automated verification**

Run:

```powershell
python -m unittest tests.test_investability_source tests.test_investability tests.test_investability_store tests.test_investability_ingestion tests.test_investability_reconciliation tests.test_investability_ui -v
python -m unittest discover -s tests -v
python -m compileall -q app.py quantdesk tests
git diff --check
```

Expected: all tests pass, compilation succeeds, and `git diff --check` prints no output.

- [ ] **Step 7: Audit representative official KRX examples**

Compare archived evidence with official screens/downloads for at least: one
management designation and release, one suspension and resumption, one
liquidation-trading period, one status published after signal close, and one
historical restriction on a currently normal security. Record source, retrieval
time, checksum, expected interval, actual interval, decision at boundary times,
and reviewer result. Any mismatch keeps investability readiness locked.

- [ ] **Step 8: Commit the completed increment**

```bash
git add quantdesk/investability_ui.py quantdesk/historical_market_ui.py tests/test_investability_ui.py tests/test_historical_market_ui.py tests/historical_market_ui_fixture_app.py README.md CHANGELOG.md
git commit -m "feat: add investability audit workflow"
```

## Final Review Gate

- [ ] Confirm every spec section maps to a task and test.
- [ ] Confirm every later-task signature matches the interface produced earlier.
- [ ] Confirm official evidence cannot be replaced or selected without audit metadata.
- [ ] Confirm current status cannot leak into historical signal or execution decisions.
- [ ] Confirm absent daily coverage is not forward-filled.
- [ ] Confirm zero volume and price anomalies remain warnings only.
- [ ] Confirm the existing 252-session strategy rule is unchanged.
- [ ] Confirm investability success does not clear corporate-action or official-backtest locks.
- [ ] Confirm existing lifecycle, historical-market, and fixture-backtest behavior remains unchanged.
