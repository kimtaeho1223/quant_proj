# Research-Only Corporate Action Engine Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exercise split/reverse-split accounting through synthetic ranking, strategy NAV, and fractional benchmark without unlocking official results.

**Architecture:** An explicit immutable research action context carries source-versioned raw-basis evidence and split events. The normal `run_backtest` and Streamlit paths retain the all-actions guard; only a caller supplying the context exercises the new accounting path, and that result is always `incomplete` without official metrics. Raw dataset prices remain unchanged; signal history alone receives an adjusted in-memory view.

**Tech Stack:** Python, pandas, Decimal, unittest.

**Spec:** `docs/superpowers/specs/2026-10-06-corporate-action-engine-integration-design.md`

## Progress (2026-10-06, updated 2026-10-07)

- Task 1 complete in `249d169`: explicit research context, canonical fingerprint, dataset-action matching, invalid-date rejection, and unchanged ordinary guard. `python -m unittest tests.test_corporate_actions tests.test_backtest -q` passed 34 tests.
- Task 2 complete in `90065d7`: only copied ranking-history closes are adjusted; original OHLC and execution opens remain raw. `python -m unittest tests.test_backtest_data -q` passed 15 tests.
- Task 3 complete in `5ffd081`: `_apply_research_splits` converts held whole-share positions before the effective session's rebalance or daily valuation, processes each event once, drops pre-event carried closes, and records an audit row without any order, cash, cost, or turnover. Fractional entitlement, missing event-day raw bar, and publication after the effective-date 09:00 open block. `python -m unittest tests.test_backtest -q` passed 24 tests.
- Task 4 complete: the fractional benchmark uses the same boundary with exact Decimal audit quantities (converted explicitly to float for existing arithmetic), and research runs now compute strategy and benchmark NAV but are always locked: `status='incomplete'`, a research-only issue, no `metrics`, `benchmark_metrics`, or `comparison`, and investability/corporate-action/official readiness flags unchanged. The previous early return was removed only together with these lock tests. In the cloud verification environment `tests.test_corporate_actions tests.test_backtest tests.test_backtest_data` passed 63 tests; `tests.test_backtest_ui` requires Streamlit, which could not be installed there, so only its four non-`AppTest` tests were run against a temporary stub. Rerun the UI module locally.
- `README.md` and `docs/investability-official-audit.md` are pre-existing uncommitted changes; preserve them.

## Global Constraints

- `ratio` is new shares / old shares; use existing `quantdesk.corporate_actions` validators and conversion functions.
- Ordinary UI and `run_backtest(dataset, config)` never accept unverified actions.
- Explicit research runs are always `incomplete`; never emit `metrics`, `benchmark_metrics`, or `comparison`.
- Stored OHLC, execution opens, and daily valuation closes stay raw; only ranking close history is adjusted.
- Effective but unpublished actions block at signal time; actions not public before effective-session open block position conversion.
- Whole-share fractional entitlement, unknown/adjusted basis, unsupported action, missing event-day raw bar, or conflicting action versions block.
- No conversion-generated order, cash flow, fee, tax, slippage, or turnover.
- Preserve `README.md` and `docs/investability-official-audit.md` changes already in the worktree.

## Review Focus

- A validated database action omitted from the context must block, not disappear (Task 1).
- A context event absent from the dataset must block to prevent injection of a fabricated action (Task 1).
- A split between rebalances must convert at the daily boundary, not wait until the next rebalance (Task 3).
- The benchmark must process the same event exactly once, including on a rebalance date (Task 4).
- A partially calculated research run must never expose official metrics after a later failure (Task 4).

---

### Task 1: Research Context and Guard Contract

**Files:**
- Modify: `quantdesk/corporate_actions.py`
- Modify: `quantdesk/backtest.py`
- Test: `tests/test_corporate_actions.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Produce immutable `ResearchActionContext(evidence: tuple[PriceBasisEvidence, ...], events: tuple[SplitEvent, ...])` with `fingerprint() -> str`, `evidence_for(code: str) -> PriceBasisEvidence`, and `events_for(code: str) -> tuple[SplitEvent, ...]`.
- Extend `run_backtest(dataset, config, external_cashflows=None, *, research_actions: ResearchActionContext | None = None)`.
- A supplied context must match the dataset's action rows by `(code, effective_date, action_type, status)` within the validation window. Reject missing, extra, duplicate, unsupported, or conflicting rows. Require verified raw evidence for every affected code. Keep the no-context guard unchanged.
- Include the context fingerprint in research input fingerprint/audit without changing ordinary dataset fingerprint behavior.

- [x] **Step 1: Write failing tests.** Verify canonical fingerprints ignore tuple order, missing/extra event blocks, duplicate event blocks, unknown basis blocks, and a no-context validated action still returns `incomplete` without metrics.
- [x] **Step 2: Run `python -m unittest tests.test_corporate_actions tests.test_backtest -q`.** Expect the new tests to fail.
- [x] **Step 3: Implement context validation and dispatch.** Validate all rows before starting NAV; use an internal research path rather than changing Streamlit's call. Explicit context alone never makes a run official.
- [x] **Step 4: Rerun the Task 1 tests.** Expect PASS; commit only these files.

### Task 2: Point-in-Time Ranking History

**Files:**
- Modify: `quantdesk/backtest_data.py`
- Modify: `quantdesk/backtest.py`
- Test: `tests/test_backtest_data.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Extend `BacktestDataset.week_inputs(signal_date, research_actions: ResearchActionContext | None = None)` and `rank_week(dataset, signal_date, minimum_ready=80, research_actions=None)`.
- In `week_inputs`, copy the full history frame for each affected code and replace only that copy's `Close` values using `split_adjusted_closes(..., signal_at=f'{signal}T15:30:00+09:00')`; preserve its `Date` and `Open` columns, and do not alter `self.prices` or `execution_open`.

- [x] **Step 1: Write failing tests.** Raw 100/50 across 2-for-1 appears 50/50 to ranking after publication; earlier signal remains raw; late publication blocks; original prices and execution open remain raw; missing evidence blocks.
- [x] **Step 2: Run the focused ranking tests.** Expect FAIL.
- [x] **Step 3: Implement the optional ranking-history view.** Pass the context from the research engine call; use existing pure price-view helper.
- [x] **Step 4: Rerun focused tests and existing `tests.test_backtest_data`.** Expect PASS; commit only these files.

### Task 3: Whole-Share Strategy Conversion and Audit

**Files:**
- Modify: `quantdesk/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Add a private event-boundary helper that takes `(holdings, last_valid, date, dataset, research_actions, fractional)` and returns converted holdings plus deterministic audit rows. Call it before `execute_rebalance` on execution dates and before `value_portfolio` on non-rebalance valuation dates; keep an applied `(code, effective_date)` set per strategy run.
- Require event publication by `effective_dateT09:00:00+09:00`, `require_event_date_raw_bar`, and `convert_split_position(..., fractional=False)` for held codes. Remove pre-event carried close for converted holdings. No trade/cash/cost row is generated by the conversion.

- [x] **Step 1: Write failing tests.** A held 10-share position becomes 20 before a 50-price event open; split between rebalances converts before daily NAV; cash, trade count, fees, and turnover do not change due to conversion. Reverse split of 5 shares at 0.5, missing raw event-day bar, or post-open publication blocks with no metrics.
- [x] **Step 2: Run focused strategy tests.** Expect FAIL.
- [x] **Step 3: Implement the boundary helper and strategy call sites.** Audit old/new quantity, event fingerprint, date, raw open/close; process each event once.
- [x] **Step 4: Rerun focused strategy and existing `tests.test_backtest`.** Expect PASS; commit only these files.

### Task 4: Fractional Benchmark and End-to-End Research Lock

**Files:**
- Modify: `quantdesk/backtest.py`
- Test: `tests/test_backtest.py`
- Test: `tests/test_backtest_ui.py`

**Interfaces:**
- Pass the same research context into `_run_fractional_benchmark`, using `convert_split_position(..., fractional=True)` at the same event boundary. Convert existing float holdings with `Decimal(str(quantity))`, keep the exact Decimal converted quantity in the event audit, then explicitly convert to float only when entering the benchmark's existing float trade/valuation arithmetic. Never mix float and Decimal implicitly. Record benchmark event audit separately.
- On a successful synthetic research simulation, return NAV and audit but force `status='incomplete'` with a research-only issue and remove all official metrics. On any failure, preserve diagnostic rows but also suppress metrics. Streamlit's loader and readiness report remain unchanged.

- [x] **Step 1: Write failing tests.** The benchmark converts 5 shares to Decimal 2.5 at 0.5 ratio; strategy and benchmark have matching event dates and no conversion costs; a rebalance-date event is applied once; repeat runs have stable fingerprint/audit; the UI and no-context runs stay locked.
- [x] **Step 2: Run focused benchmark/UI tests.** Expect FAIL.
- [x] **Step 3: Implement benchmark conversion and final lock.** Do not relax investability or official readiness flags.
- [x] **Step 4: Run `python -m unittest tests.test_corporate_actions tests.test_backtest tests.test_backtest_data tests.test_backtest_ui -q` and `git diff --check`.** Expect all PASS; commit only task files.

## Deferred

Do not migrate SQLite or import live corporate-action data in this plan. Real-data admission requires independent raw OHLC provenance, publication timestamps, revision selection, delisted-security coverage, and cash-in-lieu rules, followed by a separate readiness review.
