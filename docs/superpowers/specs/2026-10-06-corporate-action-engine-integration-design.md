# Research-Only Corporate Action Engine Integration

**Status:** Draft for review
**Date:** 2026-10-06
**Depends on:** `2026-10-06-corporate-action-price-basis-design.md`

## Purpose

Exercise split and reverse-split accounting through the complete synthetic
backtest path without claiming that current KRX prices or action rows are
verified for an official historical run. A 2-for-1 split must not look like a
50% factor loss or a 50% portfolio loss. The ordinary Streamlit path and
official readiness gates remain closed.

## Current Boundaries

`BacktestDataset.prices` stores `Date`, `Open`, and `Close` without a price
basis. `rank_week` passes those closes into factors. `run_backtest` uses raw
open/close for whole-share trading and daily valuation, and the fractional
benchmark has its own daily loop. The current engine blocks every action in
the relevant window. The pure `corporate_actions` module validates explicit
raw-basis evidence, creates a point-in-time split-adjusted close view, and
converts positions, but it is not connected to either loop.

## Selected Approach

Add an explicit research-only action context to programmatic backtests. It
contains source-versioned raw-basis evidence for every affected price series
and validated `SplitEvent` records, including a timezone-aware public
timestamp. No context means the existing all-actions guard stays in force.
The Streamlit loader and UI never create or pass a research context. Runs with
one always return `incomplete` with a research-only issue, even when synthetic
NAV and audit rows are produced; they never return official metrics or an
official-ready flag. Unsupported actions, missing evidence, conflicting
revisions, and any other invalid input block before the simulation starts.

Alternative A, immediately extending the SQLite schema and accepting real
sources, is deferred: neither the historical raw price basis nor point-in-time
action publication is verified. Alternative B, adjusting price rows in place,
is rejected because execution and valuation must continue to use raw OHLC.

## Data and Signal Flow

The context is immutable and has a deterministic fingerprint derived from
canonical evidence and event fields. It is included in the research run's
input fingerprint and audit, not silently substituted for the existing
dataset fingerprint. A matching source fingerprint alone does not establish
real-world provenance; it permits only synthetic/research execution.

For each weekly signal, the dataset continues to supply raw prices. Only the
ranking history receives an in-memory adjusted `Close` view produced at
`signal_date` 15:30 Asia/Seoul. Split factors apply only when the event is
both effective and publicly knowable by that instant. An effective but
not-yet-public event blocks the run. Execution opens, daily closes, and
stored raw rows are never adjusted. Events after the signal cannot leak into
that signal's factors.

## Position and Valuation Flow

Before the effective session's opening execution, convert each held
whole-share quantity by `new_shares / old_shares`. On a non-rebalance day,
convert before that day's valuation. The benchmark follows the same event
boundary with Decimal fractional quantities. Process an event once per
portfolio and date; never record the conversion as a trade, turnover, fee,
tax, slippage, or external cash flow. Its audit row records event fingerprint,
old/new quantity, effective session, and raw event-date open and close.
An event not publicly knowable before the effective session's open is
blocking, even if its effective date is otherwise valid.

If a whole-share reverse split creates a fractional entitlement, block the
run; do not round or invent cash-in-lieu. Require a unique, finite positive
raw open and close for the event day. Do not carry a pre-event close into
post-event valuation. A missing held-position bar, duplicate or conflicting
event, missing publication time, or adjusted/unknown price basis blocks the
run. The benchmark and strategy must agree on the date and ratio even though
their quantities can differ.

## Failure and Readiness Rules

Validation happens before any NAV is calculated where possible. A mid-run
failure returns `incomplete`, preserves already produced audit/NAV for
diagnosis, and suppresses performance metrics. No path turns the existing
investability, corporate-action, or official-backtest readiness gates on.
The UI continues to load the existing database dataset without a research
context and to show its present lock state.

## Acceptance Tests

- With 10 shares at 100 before a 2-for-1 split and raw event-day price 50,
  the holding becomes 20 shares without a fee or cash movement; factor
  history is 50/50 after publication.
- A split between weekly rebalances converts positions before daily close.
- A split effective after an earlier signal does not alter that signal.
- A reverse split with 5 whole shares and a 0.5 ratio blocks; the fractional
  benchmark can retain 2.5 shares at the same boundary.
- Missing event-day raw open/close, adjusted price basis, late publication,
  unsupported action, or conflicting versions blocks and produces no metrics.
- Repeated runs have identical research fingerprints and event audit records.
- The ordinary UI and `run_backtest` call without explicit research context
  still block actions and do not display official results.

## Deferred Real-Data Work

Verify historical raw OHLC provenance, corporate-action publication timing,
revision selection, delisted securities, and odd-lot settlement with official
sources. Only after that evidence is reviewed should SQLite migrations and a
separate official-readiness change be considered.
