# Corporate Action Accounting Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, fail-closed split/reverse-split accounting core on synthetic data without unlocking official backtests.

**Architecture:** A small `quantdesk/corporate_actions.py` module owns source contracts, point-in-time split-adjusted signal history, and position conversion. Existing backtest orchestration is unchanged in this increment; it continues to block every event. Engine integration, database migration, provider verification, and official readiness belong to later plans.

**Tech Stack:** Python, pandas, Decimal, unittest.

**Spec:** `docs/superpowers/specs/2026-10-06-corporate-action-price-basis-design.md`

## Global Constraints

- `ratio` means new shares divided by old shares; it must be finite and positive.
- Only split and reverse split are supported. Dividends and other actions remain blocked.
- Raw OHLC is immutable. Signal adjustment is an in-memory view, never a database rewrite.
- Only an independently verified `raw` basis may enter split calculations; `adjusted` and `unknown` fail closed.
- Signal history can use only events effective and publicly knowable by the signal timestamp.
- Whole-share conversion cannot round a fractional entitlement or invent cash-in-lieu.
- The existing investability, corporate-action, and official-result locks remain unchanged.

## Review Focus

- Missing or invalid basis metadata must raise, not silently default to `raw` (Task 1 test).
- Duplicate same-security effective-date events must fail rather than double-adjust (Task 2 test).
- An event effective but not yet public at signal time must block that signal (Task 2 test).
- A reverse split yielding fractional shares must fail for whole-share holdings (Task 3 test).
- Missing event-date raw open or close must fail before a held position is valued (Task 3 test).

---

### Task 1: Explicit Price and Action Evidence Contracts

**Files:**
- Create: `quantdesk/corporate_actions.py`
- Create: `tests/test_corporate_actions.py`

**Interfaces:**
- Produce `CorporateActionError(ValueError)`.
- Produce immutable `PriceBasisEvidence(code, basis, provider, retrieved_at, source_version, fingerprint)` and `SplitEvent(code, effective_date, published_at, action_type, ratio, status, source_fingerprint)` records.
- Produce `require_raw_basis(evidence: PriceBasisEvidence, code: str) -> None` and `validate_split_event(event: SplitEvent) -> None`.
- Parse timestamps as timezone-aware instants. Reject missing fields, nonpositive or nonfinite ratios, unsupported action types, and non-`validated` status.

- [ ] **Step 1: Write failing tests.** Test a complete raw evidence record, rejected `adjusted`/`unknown`/missing metadata, a valid 2-for-1 event, zero/NaN ratio, missing timezone, and unsupported dividend.
- [ ] **Step 2: Run `python -m unittest tests.test_corporate_actions -q`.** Expect failures because the module and interfaces do not exist.
- [ ] **Step 3: Implement the records and validators.** Use `Decimal` for ratios, ISO dates, and aware ISO timestamps; do not infer a missing source field.
- [ ] **Step 4: Rerun the Task 1 tests.** Expect PASS.
- [ ] **Step 5: Commit only this task's module and tests** after reviewing the staged diff; preserve unrelated local documentation changes.

### Task 2: Point-in-Time Signal Price View

**Files:**
- Modify: `quantdesk/corporate_actions.py`
- Modify: `tests/test_corporate_actions.py`

**Interfaces:**
- Consume the Task 1 records and validators.
- Produce `split_adjusted_closes(prices: pd.DataFrame, evidence: PriceBasisEvidence, events: Sequence[SplitEvent], signal_at: str) -> pd.DataFrame` with `Date` and `Close` columns.
- For each event with `effective_date <= signal date` and `published_at <= signal_at`, divide only pre-effective raw closes by its ratio. If an event is effective but not yet public at the signal, raise instead of calculating a misleading factor. Apply multiple eligible events in effective-date order. Preserve the source DataFrame.

- [ ] **Step 1: Write failing tests.** For raw closes 100 before and 50 after a 2-for-1 split, expect a 50/50 signal view after publication; if the event is effective but not yet public, expect an error. Test a future effective date, two successive events, unchanged input, unknown basis, an event for a different code, and duplicate same-date events.
- [ ] **Step 2: Run `python -m unittest tests.test_corporate_actions -q`.** Expect failures in the new signal-view tests.
- [ ] **Step 3: Implement `split_adjusted_closes`.** Validate evidence and event sequence first; reject ambiguous duplicates.
- [ ] **Step 4: Rerun the Task 2 tests.** Expect PASS.
- [ ] **Step 5: Commit only this task's files** after reviewing the staged diff.

### Task 3: Whole-Share and Fractional Position Conversion

**Files:**
- Modify: `quantdesk/corporate_actions.py`
- Modify: `tests/test_corporate_actions.py`

**Interfaces:**
- Consume the Task 1 records and validators.
- Produce `convert_split_position(quantity: int | Decimal, event: SplitEvent, *, fractional: bool = False) -> int | Decimal`.
- Produce `require_event_date_raw_bar(prices: pd.DataFrame, evidence: PriceBasisEvidence, event: SplitEvent) -> tuple[float, float]` returning raw open and close.
- Conversion changes quantity only: no trade, cash movement, fee, or turnover. Whole-share output must be integral; fractional benchmark output may be Decimal.

- [ ] **Step 1: Write failing tests.** Assert 10 shares become 20 for a 2-for-1 split; 5 shares at ratio 0.5 raises in whole-share mode and becomes Decimal 2.5 in fractional mode. Assert zero/negative holdings, adjusted-basis prices, and missing/nonpositive event-date open or close raise.
- [ ] **Step 2: Run `python -m unittest tests.test_corporate_actions -q`.** Expect failures in the new conversion/open tests.
- [ ] **Step 3: Implement the two functions.** Require a verified event before conversion and a raw event-date open before any later valuation.
- [ ] **Step 4: Run `python -m unittest tests.test_corporate_actions tests.test_backtest tests.test_backtest_ui tests.test_backtest_data -q` and `git diff --check`.** Expect all tests PASS and no whitespace errors; verify existing corporate-action guard still blocks official metrics.
- [ ] **Step 5: Commit only this task's files** after reviewing the staged diff.

## Deferred Integration

A separate plan will add versioned price/action storage, wire the signal view into ranking, convert positions before the event-date open in both the whole-share strategy and fractional benchmark, and emit event audit records. Its tests must cover a split between rebalances, missing prices, raw/adjusted double counting, and stable fingerprints. Until that integration and real-source verification are complete, every action remains blocked by engine version 0.4.1 or later and the official UI stays locked.
