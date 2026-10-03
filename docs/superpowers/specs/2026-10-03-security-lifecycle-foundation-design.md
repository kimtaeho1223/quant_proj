# Security Lifecycle Foundation Design

## Purpose

Build the first security-lifecycle increment for Quant Desk KR so historical
KOSPI and KOSDAQ membership can be reconstructed without using today's listing
as a filter. The module must retain securities that later delisted, exclude
securities before they listed, and preserve official evidence for every derived
identity and effective-date interval.

This increment is a prerequisite for production backtests. It does not unlock
official performance reporting by itself because trading-status eligibility and
corporate-action outcomes remain separate later increments.

## Agreed Scope

The first implementation delivers:

- immutable archival of official KRX security-master, new-listing, and delisting
  source files;
- source checksums, retrieval metadata, parser versions, and audit findings;
- separate issuer, security, and effective-dated identifier records;
- official lifecycle events for listing, market transfer, code change, name
  change, and delisting;
- deterministic derivation of effective-dated code, name, market, and security
  classification intervals;
- reconstruction and fingerprint verification from archived sources;
- strict conflict quarantine and readiness diagnostics;
- a small read interface for historical listed securities and range readiness;
- Streamlit collection, review, timeline, and rebuild diagnostics.

The schema and interfaces must allow later additions for trading status,
investability, corporate actions, and delisting settlement without migration of
the lifecycle concepts introduced here.

## Deferred Work

The following are explicitly outside this implementation:

- deciding whether a listed security was tradable on a given session;
- management designation, trading suspension, liquidation trading, or liquidity
  eligibility;
- splits, reverse splits, mergers, spin-offs, tender offers, or quantity and
  price adjustment;
- delisting settlement proceeds;
- carrying momentum or cost basis across predecessor and successor securities;
- removing the official-backtest readiness lock.

The module may detect evidence suggesting deferred corporate-action work. Such
evidence blocks the affected interval; it does not attempt an adjustment.

## Design Principles

### Preserve all observed securities

Ingestion never joins against the current listing to decide which rows to keep.
Preferred shares, SPACs, REITs, delisted securities, and unresolved instruments
remain in source and lifecycle storage even when the strategy will later exclude
them.

### Separate issuer, security, and identifier

An issuer may have several securities. A security may have several identifiers
and names over time. The internal `security_id` is not a six-character stock
code or an ISIN. Codes and ISINs are effective-dated identifiers attached to the
internal security. The internal `issuer_id` groups securities only when official
evidence supports that relationship.

Code reuse with a different official security identity creates a separate
security. A merger, split, or relisting is represented by an explicit lineage
relationship and never by silently reusing price or factor history.

### Preserve two kinds of time

Every event records both:

- effective time: when the exchange fact became true; and
- knowledge time: when the source version was retrieved and accepted locally.

Corrected official files create new source versions and derived revisions. They
do not overwrite the evidence used by an earlier run.

### Fail closed

Unknown classifications, unsupported relationships, conflicting official
sources, and incomplete files remain unresolved. No name heuristic, forward
fill, or current-state lookup may convert unresolved evidence into an eligible
historical security.

## Architecture

### Official Source Adapters

Adapters acquire KRX full security-master, new-listing, and delisting records.
Automatic acquisition is preferred. An official CSV import adapter is retained
for provider outages and must feed the identical archival and validation path.
Provider-specific columns, encodings, and request mechanics remain private to
the adapters.

### Raw Archive

The archive writes a complete file atomically, calculates SHA-256, and records
source type, requested range, fetched time, source URL or official screen,
content length, and parser version. Partial files are never registered. A second
payload for the same logical request is stored as a new version.

### Lifecycle Builder

The builder consumes selected raw versions and returns a candidate lifecycle
model plus structured findings. It normalizes official fields, resolves only
evidence-backed identities, creates append-only events, derives intervals, and
calculates a deterministic fingerprint. It does not read the current listing or
the backtest tables.

### Lifecycle Store

SQLite stores raw manifests, parsed source rows, identities, events, intervals,
lineage relationships, findings, source selections, rebuild records, and model
fingerprints. Candidate data is written to staging tables and promoted in one
transaction only after hard validations pass.

### Read Interface

Callers use one deep module rather than reading lifecycle tables directly. Its
external interface exposes only:

- `listed_securities(as_of_date)` for exchange-listed securities and their
  effective attributes;
- `lifecycle_timeline(security_id)` for auditable events and intervals; and
- `readiness(start_date, end_date)` for coverage, unresolved conflicts, source
  fingerprints, and blocking reasons.

Investable-universe selection will be a later interface. The backtest engine may
not treat `listed_securities()` as equivalent to tradable or strategy-eligible.

## Logical Data Model

The model includes the following concepts:

- `lifecycle_raw_versions`: immutable source manifests and archive paths;
- `lifecycle_source_rows`: normalized rows tied to a raw version and source row;
- `issuers`: internal issuer identities and official references;
- `securities`: internal security identities and security-kind status;
- `security_identifiers`: effective-dated stock codes, ISINs, and official IDs;
- `security_names`: effective-dated Korean names;
- `lifecycle_events`: append-only official events with effective and knowledge
  time;
- `security_lineage`: evidence-backed predecessor/successor relationships whose
  type never implies automatic return continuity;
- `security_intervals`: derived effective periods for market, code, name, and
  classification;
- `lifecycle_findings`: warning, quarantine, and blocking diagnostics;
- `lifecycle_source_selections`: selected raw versions and resolution evidence;
- `lifecycle_rebuilds`: parser/rule versions, fingerprints, counts, and result.

Derived rows always retain links to source rows and raw versions. Variable audit
details may use sorted UTF-8 JSON; identity, event, interval, and status fields
remain relational and constrained.

## Event and Interval Semantics

- Listing date is inclusive.
- Delisting effective date is exclusive for listed-membership queries.
- Last trading date and delisting effective date are distinct fields.
- Name changes do not create a new security.
- A market transfer closes one market interval and opens another only when an
  official event links the records.
- A code change closes the old identifier interval and opens the new interval.
- A six-character code may be reused only in non-overlapping intervals and may
  refer to different internal securities.
- Suspended trading does not end a listing interval.
- A missing daily-market row is never interpreted as a delisting event.
- Security classification is effective-dated. Unknown or conflicting
  classifications fail closed.

## Validation Policy

Hard validation rejects or quarantines a candidate model when:

- required source fields, source metadata, or checksums are absent;
- a source file is empty, partial, unexpectedly shaped, or internally
  duplicated;
- the same stock code is active for more than one security on the same date;
- identifier, market, name, or classification intervals overlap illegally;
- an event sequence has impossible ordering;
- listing, last-trading, and delisting dates conflict;
- an identity or lineage link requires unsupported inference;
- official sources disagree on a material attribute;
- the same logical source request produces a new checksum without review;
- a lifecycle interval cannot be traced to official source rows; or
- a rebuild from selected raw versions changes the canonical fingerprint.

Cross-checking against canonical daily full-market data reports separately:

- a market row with no active lifecycle interval;
- an active lifecycle security missing from the market snapshot;
- market or name disagreement;
- listed-share or price discontinuities that may indicate an unvalidated
  corporate action; and
- date-level count changes outside established completeness thresholds.

Absence alone never proves delisting. Until later trading-status sources explain
an absence, the affected security and date remain unresolved rather than being
deleted or forward-filled.

Findings have four levels:

- pass: all required evidence and invariants hold;
- warning: auditable non-blocking information;
- quarantine: a source version cannot be promoted;
- blocking: the affected identity, interval, or date cannot feed downstream
  historical membership.

There is no generic override. Resolution selects replacement official evidence
or records a source-row-based explanation, actor, and timestamp.

## Processing Flow

1. Acquire or import an official file.
2. Archive it atomically and register its checksum.
3. Parse into source rows without applying current-listing filters.
4. Build candidate identities and append-only events.
5. Derive effective-dated intervals.
6. Run internal, cross-source, and daily-market reconciliation.
7. Promote the candidate lifecycle model transactionally when no quarantine or
   blocking finding applies to the promoted scope.
8. Rebuild from selected raw versions and compare canonical fingerprints.
9. Publish lifecycle readiness while retaining the corporate-action and
   official-backtest locks.

Reruns are idempotent. A process interruption cannot leave a partially promoted
model. Existing promoted data remains readable when a later acquisition fails.

## Streamlit Workflow

The historical-market page adds a Security Lifecycle section showing:

- source-by-source acquisition state and selected raw versions;
- automatic collection and official CSV fallback actions;
- file name, source, retrieval time, checksum, parser version, and row counts;
- event counts for listing, transfer, code change, name change, and delisting;
- pass, warning, quarantine, and blocking counts;
- issuer-to-security-to-identifier timelines;
- cross-source and daily-market conflicts with source-row evidence;
- deterministic rebuild action and fingerprint comparison; and
- separate indicators for lifecycle readiness, investability readiness,
  corporate-action readiness, and official-backtest readiness.

The UI never edits canonical lifecycle rows directly and never exposes a button
that converts a conflict into a pass without evidence.

## Testing Strategy

Unit and integration fixtures prove that:

- future listings never appear in earlier snapshots;
- a later-delisted security remains listed before its delisting date;
- suspension or source absence is not interpreted as delisting;
- listing dates are inclusive and delisting dates exclusive;
- last trading date remains distinct from delisting effective date;
- a market transfer and code change close and open the correct intervals;
- reused codes create distinct securities when official identity differs;
- common and preferred shares remain distinct securities under one issuer;
- unknown classifications fail closed;
- lineage never automatically carries price or factor history;
- source corrections create new versions instead of overwriting evidence;
- conflicting official rows are quarantined;
- incomplete downloads and malformed schemas cannot enter storage;
- promotion rolls back completely after an injected failure;
- identical reruns are idempotent;
- raw reconstruction reproduces row counts and fingerprints;
- no current-listing lookup can remove a historical security; and
- unresolved findings keep downstream readiness blocked.

Network calls are replaced by deterministic source-adapter fixtures in automated
tests. Representative real KRX examples for a new listing, delisting, market
transfer, code change, and preferred share are manually compared with official
screens or downloads before production use.

## Staged Delivery

### Increment 1: lifecycle foundation

This design and the next implementation plan cover raw evidence, identities,
events, intervals, rebuilding, readiness, and audit UI.

### Increment 2: investability and trading status

Add the strategy's investability policy over the lifecycle classification
evidence, including SPAC and REIT treatment, plus management designation,
suspension, liquidation trading, daily reconciliation, and distinct
listed-versus-investable snapshots. This increment uses the lifecycle read
interface rather than bypassing it.

### Increment 3: corporate actions and outcomes

Add split, reverse-split, merger, spin-off, successor, quantity adjustment,
delisting settlement, and price-basis validation. Only this increment may clear
the remaining corporate-action readiness lock.

## Completion Criteria

Increment 1 is complete when:

- all new and existing automated tests pass;
- selected official KRX files can be archived, parsed, and rebuilt
  deterministically;
- identities, events, and intervals are traceable to source rows;
- current-listing data cannot filter historical records;
- conflicts and unknown classifications fail closed;
- interrupted or failed imports cannot corrupt a promoted model;
- the Streamlit audit view explains every blocking finding; and
- the application clearly reports that investability, corporate actions, and
  official production backtesting remain locked.
