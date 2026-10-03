# Point-in-Time Investability and Trading Status Design

**Status:** Approved for implementation planning
**Date:** 2026-10-03
**Depends on:** Security lifecycle foundation

## Purpose

Build an auditable point-in-time layer that distinguishes a security being
listed from it being eligible for the strategy or executable on a particular
session. The layer must reconstruct historical KOSPI and KOSDAQ common-share
investability without using present-day status and must preserve official
evidence for every inclusion, exclusion, and unresolved decision.

This increment is deliberately conservative. Missing or conflicting official
status does not become an inferred tradable state. It becomes `unknown` and
keeps the affected security-date interval out of official backtests.

## Agreed Policy

The initial strategy universe allows only:

- KOSPI and KOSDAQ securities;
- officially classified common shares; and
- securities without an effective management designation, trading suspension,
  or liquidation-trading status at the relevant decision time.

Preferred shares, SPACs, REITs, ETFs, ETNs, and unknown classifications are
excluded. The policy is versioned rather than embedded as permanent domain
truth so a future strategy can make a different choice without rewriting the
historical status model.

A 252-session price-history requirement is not part of base investability. It
is a strategy-readiness rule applied later by the point-in-time ranking adapter.

## Goals

- Archive immutable official KRX status evidence with checksums and retrieval
  metadata.
- Model management designation, suspension, resumption, liquidation trading,
  and status removal as effective intervals linked to lifecycle `security_id`.
- Preserve both when a state became effective and when it was publicly knowable.
- Produce deterministic, versioned point-in-time investability decisions.
- Reconcile official status against the security lifecycle and canonical daily
  market data without inferring official status from prices or volume.
- Recheck execution eligibility on the next trading session separately from
  signal-date universe eligibility.
- Keep the corporate-action and official-backtest readiness locks closed.

## Non-Goals

- Corporate-action adjustment, successor quantity conversion, or delisting
  settlement.
- Cash-dividend total-return calculations.
- Factor computation or the 252-session strategy-history rule.
- Automatic order transmission.
- Treating zero volume, a missing price, or a large price move as authoritative
  evidence of suspension or resumption.
- Automatically approving ambiguous source revisions.

## Domain Separation

The application keeps four concepts distinct:

1. **Listed:** the lifecycle interval says the security legally exists on the
   market for the date.
2. **Policy eligible:** its market and official security classification are
   allowed by the selected investability policy.
3. **Tradable:** official status permits a new order for the relevant session.
4. **Strategy ready:** the security additionally has the required price and
   financial history for factor calculation.

The investability layer owns items 2 and 3. It consumes item 1 and leaves item 4
to the backtest data adapter.

## Recommended Source Strategy

The primary record is an official daily status snapshot. Official designation
and release notices are secondary evidence used to cross-check interval
boundaries. Price and share-count anomalies are tripwires only.

Automatic retrieval may be attempted, but an authenticated or unstable KRX
endpoint must not be bypassed. The application supports official CSV fallback
through the audit UI. Browser cookies, login sessions, and copied private API
calls are not accepted as source inputs.

Every raw object records:

- dataset type and requested effective date;
- source URL or official screen identifier;
- retrieval timestamp;
- exact raw bytes, encoding, and SHA-256 checksum;
- parser version and selected source version;
- declared coverage and row count; and
- an optional official correction reference.

Raw versions are append-only. A changed response for the same request becomes a
new version and locks readiness until the selected version has an explicit
reason.

## Time Model

Every status assertion carries separate time dimensions:

- `effective_from` and `effective_to`: the half-open interval in which the
  status legally or operationally applies;
- `known_at`: the earliest timestamp at which the official evidence was public;
  and
- `observed_at`: when Quant Desk retrieved the evidence.

The engine may use an assertion only when `known_at` is on or before the
decision timestamp. A later correction may change future decisions but cannot
silently rewrite a previously valid point-in-time view. Source revisions and
rebuilds remain visible in the audit trail.

Date-only official evidence uses a documented conservative timestamp rule. If
the publication time cannot be proven, the status becomes usable no earlier
than the next market session. This prevents same-day look-ahead.

## State Model

The normalized model supports these independent flags:

- `management_designation`;
- `trading_suspension`;
- `liquidation_trading`; and
- `official_status_unknown`.

Each interval links to `security_id`, raw version, source row, effective dates,
and knowledge time. Independent flags avoid collapsing distinct official
states into a lossy single label.

The point-in-time decision is one of:

- `eligible`: listed, policy allowed, and no blocking status;
- `ineligible`: official evidence proves at least one policy exclusion;
- `unknown`: required evidence is missing, conflicting, unselected, or not yet
  knowable; or
- `not_listed`: the lifecycle model proves no listing interval exists.

Only `eligible` may enter a signal universe or accept a new order.

## Components

### `InvestabilityArchive`

Stores compressed immutable raw evidence below
`data/raw/krx/investability`, addressed by SHA-256. It follows the lifecycle
archive's atomic-write and path-containment rules.

### `TradingStatusModel`

Parses selected source rows, links them to lifecycle identities, and constructs
effective intervals. Duplicate, overlapping, impossible, or unidentified rows
produce findings instead of silent precedence rules.

### `InvestabilityPolicy`

Defines allowed markets, security kinds, and blocking status flags. Every policy
has a stable identifier, semantic version, canonical JSON representation, and
fingerprint.

### `InvestabilityReconciler`

Compares status intervals with lifecycle listing intervals and canonical daily
market observations. It detects missing coverage, observations outside listing
intervals, conflicting status, unexpected market rows, and suspicious status
boundaries. Reconciliation never manufactures an official state.

### `InvestabilityStore`

Persists raw versions, normalized assertions, intervals, policy versions,
findings, source selections, rebuilds, manual reviews, and readiness summaries
in the existing SQLite market database. Promotion is transactional.

### `PointInTimeUniverse`

Provides a storage-independent read interface:

```python
decision(security_id, as_of_timestamp, policy_id) -> InvestabilityDecision
universe(as_of_timestamp, policy_id) -> DataFrame
execution_status(security_id, session_date, decision_timestamp) -> ExecutionStatus
readiness(start_date, end_date, policy_id) -> InvestabilityReadiness
```

Returned rows include the decision, reason codes, evidence identifiers, policy
version, and model fingerprint. Callers must not query physical tables.

### `InvestabilityAuditUI`

Adds an audit section under historical market data for automatic collection,
official CSV fallback, source selection, deterministic rebuild, conflict review,
coverage, and per-security decision inspection. It displays separate lifecycle,
investability, corporate-action, and official-backtest locks.

## Processing Flow

1. Archive the official daily snapshot or official fallback CSV.
2. Validate checksum, encoding, schema, requested date, coverage, and row count.
3. Normalize rows while retaining raw row identity.
4. Link rows to lifecycle `security_id`; quarantine unresolved or ambiguous
   identities.
5. Build independent status intervals with effective and knowledge times.
6. Reconcile intervals with lifecycle and daily-market evidence.
7. Select one source version per coverage key with a documented reason for any
   revision.
8. Promote the full candidate model in one SQLite transaction only when hard
   validations pass.
9. Rebuild from selected raw evidence and compare the deterministic fingerprint.
10. Publish date- and policy-scoped readiness without clearing later locks.

## Signal and Execution Contract

At the weekly signal timestamp, `PointInTimeUniverse.universe` returns only
securities that were listed, policy eligible, and officially tradable using
information knowable at that time.

Before the next-session order is executed, the engine checks
`execution_status` again:

- a newly suspended or liquidation-trading security cannot receive a new buy or
  sell fill;
- an unfilled order is recorded with an evidence-backed reason;
- an existing suspended holding may carry its last validated close for
  valuation only;
- carried prices are never used for factor calculation or execution; and
- a security that never resumes requires validated corporate-action or
  delisting-settlement evidence before an official result can complete.

## Validation and Readiness

Hard blockers include:

- a requested date without selected official coverage;
- a source revision without an explicit selection reason;
- a status row that cannot be linked to exactly one lifecycle security;
- contradictory active intervals for the same flag;
- an effective interval outside the security's listing interval;
- unavailable or future `known_at` evidence for the requested decision;
- lifecycle or daily-market reconciliation quarantine;
- a rebuild fingerprint mismatch; or
- unresolved manual review required by a hard rule.

Readiness is computed for a requested date range and policy version. A passing
date elsewhere does not conceal an unresolved date in the requested range.
Results expose covered dates, unknown dates, blocked securities, findings,
source selections, policy fingerprint, and model fingerprint.

## Error Handling

- Network and authentication errors preserve the existing valid model and
  produce actionable fallback guidance.
- A partial download is never promoted.
- Missing daily evidence becomes `unknown`; it is not forward-filled.
- Conflicting official evidence is quarantined until source selection or manual
  review resolves it.
- A failed date does not delete already validated dates.
- Parser failures preserve raw bytes and diagnostic metadata.
- Manual review cannot edit raw or normalized facts. It records a decision,
  reason, reviewer, time, and referenced evidence.

## Storage and Rebuild

Use a dedicated `investability_schema` version alongside the existing lifecycle
and historical-market schemas. Logical tables cover:

- raw versions and source selections;
- normalized status assertions;
- effective status intervals;
- policy definitions;
- reconciliation runs and findings;
- promoted model metadata;
- rebuild verification; and
- manual reviews.

Canonical ordering, sorted UTF-8 JSON, normalized dates, and stable null
handling feed the model fingerprint. Raw files and `data/market.db` must be
backed up together and remain excluded from Git.

## Testing Strategy

Pure unit fixtures cover:

- management designation and release;
- suspension and resumption;
- liquidation trading followed by delisting;
- signal eligibility followed by execution-day suspension;
- a security that is normal today but restricted historically;
- future official knowledge excluded from an earlier decision;
- missing, duplicate, overlapping, and conflicting status rows;
- source revision selection and unselected-revision locks;
- lifecycle identity ambiguity;
- deterministic rebuild fingerprints;
- atomic rollback on failed promotion; and
- official-run blocking for any `unknown` interval.

Integration tests cover official CSV archive-to-query flow, daily-market
reconciliation, Streamlit audit states, and unchanged existing fixture-backtest
behavior. A release audit compares representative real KRX cases for each
supported status transition against official screens or downloads.

## Security and Privacy

- Authentication tokens, browser cookies, and login sessions are never stored.
- Uploaded filenames are untrusted; archive paths are generated internally.
- CSV formulas and markup remain data and are never executed.
- SQLite writes use parameters and transactions.
- Raw downloads have bounded size and validated content type.
- Diagnostics redact secrets and keep public source identifiers only.

## Rollout

1. Implement archive, parsing, and deterministic fixtures.
2. Add interval construction and policy decisions.
3. Add transactional storage and read interfaces.
4. Add lifecycle and daily-market reconciliation.
5. Add signal/execution integration behind readiness locks.
6. Add the Streamlit audit workflow and official CSV fallback.
7. Validate representative official KRX examples.

This increment may clear only the investability readiness lock. Corporate-action
and official-backtest readiness remain locked until their own evidence and
validation increments are complete.

## Completion Criteria

- All existing and new automated tests pass.
- Every decision is traceable to policy, model fingerprint, and official source
  rows.
- Point-in-time queries never use evidence with a future `known_at`.
- Missing or conflicting status cannot silently become eligible.
- Signal-date and execution-date decisions are tested independently.
- Selected official raw evidence rebuilds to the same fingerprint.
- Existing lifecycle and fixture-backtest behavior remains unchanged.
- The UI clearly reports why each readiness lock is open or closed.
