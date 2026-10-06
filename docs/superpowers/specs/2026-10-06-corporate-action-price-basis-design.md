# Point-in-Time Price Basis and Corporate Actions

**Status:** Draft for review
**Date:** 2026-10-06
**Depends on:** Historical market data, security lifecycle, investability status

## Purpose

Prevent stock splits and reverse splits from creating false factor returns or
portfolio gains and losses. This design starts with deterministic synthetic
examples. It does not claim that any currently stored price or action source is
ready for an official historical backtest.

## Current Gap

The backtest reads `Open` and `Close` directly for ranking, execution, daily
valuation, and its fractional benchmark. The stored corporate-action record has
an effective date, type, ratio, status, and source, but no publicly knowable
timestamp or explicit price basis. Before engine version 0.4.1, an action marked
`validated` could pass the guard without changing quantities or returns.
Version 0.4.1 blocks every action in the validation window until this design is
implemented and verified.

## Scope

- First accounting types: ordinary share split and reverse split only.
- Keep the existing dividend-excluded price-return definition. Dividend cash
  accounting, rights, spin-offs, mergers, and delisting settlement are later
  work; any such event remains blocking when present.
- Preserve raw OHLC values. Never silently overwrite them with adjusted prices.
- Record a per-series price basis (`raw`, `adjusted`, or `unknown`), provider,
  retrieval time, source version, and fingerprint. Only an independently
  verified `raw` basis may feed split accounting.
- Require an immutable action source, effective session, ratio convention,
  public availability timestamp, and selected version. Missing or conflicting
  evidence is blocking.

## Accounting and Timing

Represent the ratio as `new_shares / old_shares`, strictly positive and finite.
Before the effective session's opening trade, convert held quantity by that
ratio. Do not generate an order, turnover, fee, or external cash flow for the
conversion. With a raw price series, the post-event open and close remain raw.
Reconcile pre-event quantity times the last pre-event raw close against the
converted quantity times the event-date raw price, allowing an actual market
move but never assuming exact equality as proof of source correctness.

Whole-share portfolios may produce fractional entitlements in a reverse split.
The engine must not round them or invent cash-in-lieu. It blocks that run unless
an official settlement amount and payment timing are separately modeled. The
fractional benchmark can retain fractional quantities, but it must use the same
event boundary and raw price basis. Event processing must be deterministic
across weekly rebalances and daily valuations, including when the event falls
between two rebalances. If the effective session lacks a usable raw price,
the engine blocks instead of carrying an unconverted pre-event close into a
post-event valuation.

At each signal time, momentum and volatility use a historical price view whose
split factors include only events both effective and publicly knowable by that
signal. Adjustment
is in memory for factor calculation; stored raw bars are unchanged. An event
published later cannot be applied to an earlier signal, even if its effective
date is earlier. If the event was already effective but not knowable at the
signal, the factor calculation blocks rather than treating the raw price jump
as an ordinary return. Execution and valuation use raw prices and converted
quantities, not the factor-adjusted signal series.

## Boundaries and Readiness

The first implementation slice is a pure, tested split/reverse-split accounting
component and price-basis validation using synthetic fixtures. It does not
unlock the official UI or declare any real provider's series to be raw.
Integrating it into the engine requires the ranking path, whole-share strategy,
fractional benchmark, and audit output to consume the same validated event
model. An unsupported type, unknown price basis, missing public timestamp,
unresolved fractional entitlement, or source revision without a selection
reason keeps the result `incomplete` and suppresses official metrics.

The current investability lock is independent and remains closed until its
official full-market evidence is separately verified. Even a complete split
implementation cannot clear it.

## Acceptance Checks

- A 2-for-1 split changes 10 held shares to 20 before the effective open;
  a 100-to-50 raw price change alone does not halve portfolio value.
- A reverse split producing a fractional entitlement blocks rather than
  rounding or fabricating cash.
- A split after a signal does not alter that signal's factors. A split already
  public by a later signal can adjust that later signal's historical view.
- The whole-share strategy and fractional benchmark agree on event timing.
- Adjusted or unknown-basis bars cannot be combined with quantity conversion.
- Unsupported actions and unverified sources remain blocking; no official
  performance is shown.
- Tests cover repeated runs and source fingerprints so audit output is stable.

## Open Evidence Questions

- Which official historical OHLC source is demonstrably raw, including
  corrections and delisted securities?
- Which official action source provides ratio, effective session, and public
  availability time for the target period?
- How are odd lots and cash-in-lieu actually settled for each reverse split?

No real-data readiness flag changes until samples and the relevant official
source rules answer these questions.
