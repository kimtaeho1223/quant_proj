# Point-in-Time Backtest Engine Design

## Purpose

Build a transparent, reproducible backtest engine for the Quant Desk KR weekly multifactor strategy. The first validation window is three years, while the data and engine contracts must support a later ten-year run without redesign.

The engine is for personal research. It does not place orders, claim that historical performance will repeat, or silently produce results from incomplete point-in-time data.

## Agreed Strategy

- Initial capital is KRW 10,000,000.
- The universe is the 100 largest eligible KOSPI and KOSDAQ listings by market capitalization at each signal date.
- Preferred shares, SPACs, REITs, and non-common-stock listings are excluded using information valid on that date.
- Factor weights are fixed at momentum 45%, value 20%, quality 20%, and low volatility 15%.
- The portfolio targets 20 equal-weight positions.
- An existing position ranked 30 or better is retained; remaining slots are filled from the highest-ranked names.
- The minimum order value is KRW 100,000 and quantities are whole shares.
- The initial aggregate transaction-cost assumption is 0.3% per trade side. Internally, fees, taxes, and slippage are separate fields so they can be replaced later without changing the engine.
- Factor-weight optimization is outside the first version.

## Time Model

The signal date is the final trading day of each week. The engine may use only information available by that day's market close.

The strategy forms its target portfolio after the signal close and executes orders at the next trading day's opening price. A Friday close can influence the signal but can never be used as that signal's execution price. When Friday is a holiday, the latest trading day in that week becomes the signal date.

The requested performance period is separate from the warm-up period. A three-year performance run requires at least 252 earlier trading days of prices and financial statements filed before the first signal date. Warm-up observations do not contribute to reported performance.

## Point-in-Time Data Rules

### Universe

Every signal date uses a historical market snapshot for all KOSPI and KOSDAQ listings. The engine constructs the top 100 from that snapshot rather than applying today's listing to the past. Securities that later delisted remain eligible when they were valid members at the historical signal date. Securities that had not yet listed cannot enter.

The snapshot must contain the effective trading date, stock code, name, market, security classification, market capitalization, and trading value. A current-only listing snapshot is not a valid historical backtest input.

### Prices

Momentum and volatility use the most recent 252 valid closes on or before the signal date. Orders use the following trading day's open. Holdings are valued daily at close to produce the NAV series and drawdown.

The engine does not substitute a close for a missing execution open. An unavailable execution price leaves the order unfilled and records the reason. Corporate actions must be represented by validated adjusted prices or explicit position adjustments. An ambiguous split, merger, or delisting marks the affected interval as invalid rather than assuming a favorable price.

A temporarily suspended holding is valued at its last valid close until trading resumes. This carry-forward is permitted only for valuation, never for execution or factor calculation. A security that does not resume trading requires a validated delisting, merger, or settlement record before the run can be considered complete.

### Financial Statements

The first version uses annual statements only. For each signal date it selects the latest eligible annual statement whose filing timestamp is on or before the signal date. Consolidated statements are preferred to separate statements for the same business year. A corrected filing becomes eligible only on its own correction filing date; it cannot rewrite earlier signals.

The statement remains in use until a later eligible annual filing becomes available. Quarterly, semiannual, and TTM calculations are deferred.

### Dividends

The first version reports price returns and excludes cash dividends. This limitation is shown with every result. The ledger accepts future external cash-flow records so dividends can be added later without changing the strategy interface.

## Components

### Point-in-Time Data Layer

Provides immutable snapshots to the engine:

- historical universe snapshot for a signal date;
- price history ending at the signal date;
- next-session opening prices;
- daily closing prices for valuation;
- latest eligible annual statement per security;
- corporate-action validation status;
- KOSPI and KOSDAQ reference-index observations.

This layer reads the existing market and DART stores where their data satisfies the contract. Historical market snapshots and corporate-action records require new storage. Data collection remains separate from simulation.

### Strategy Layer

Calculates point-in-time factors, percentile scores, ranks, retained positions, and target securities. The existing factor scoring behavior remains the reference implementation. Target selection is separated from order execution so signal closes cannot override next-session opening prices.

### Portfolio and Execution Layer

Maintains cash and whole-share positions, generates sells before buys, applies minimum-order and cash constraints, records separate cost components, and prevents negative cash. It emits an auditable ledger containing every requested, filled, skipped, and rejected order.

### Analytics Layer

Builds daily strategy NAV, weekly holdings, trades, costs, turnover, and performance statistics. It calculates cumulative return, CAGR, maximum drawdown, annualized volatility, Sharpe ratio, turnover, and excess return. The first version uses a 0% annual risk-free rate for the Sharpe ratio, and stores this convention with each run.

### Streamlit Layer

Adds a Backtest page with:

- start and end dates plus read-only strategy assumptions;
- data-readiness results before execution;
- cumulative-return comparison and summary metrics;
- weekly positions, trades, turnover, and costs;
- an audit view for the universe, filings, prices, and exclusions used by any selected week.

The first version keeps factor weights and strategy rules fixed. The page reads stored source data and never sends a brokerage order.

## Benchmarks

The primary benchmark is an equal-weight portfolio of the same point-in-time top-100 universe. It starts with the same KRW 10,000,000, holds whole shares, rebalances weekly, and uses the same next-session execution timing, minimum-order rule, and transaction-cost model as the strategy. Unallocatable amounts remain in benchmark cash. This comparison isolates the effect of multifactor selection from broad universe performance.

KOSPI and KOSDAQ index returns are shown separately as secondary market context. They are not combined into an arbitrary blended index. The UI identifies whether each index series is a price or total-return series; the first version compares price-return series consistently with the dividend-excluding strategy.

## Data Readiness and Failure Policy

Before a run, the engine reports valid and invalid weeks with reasons. A week is eligible only when all required historical snapshots and execution sessions exist and at least 80 of the top-100 candidates have valid factor inputs.

Missing or invalid data is never imputed solely to make a run complete. The engine records and rejects:

- missing historical universe snapshots;
- fewer than 80 factor-ready candidates;
- insufficient 252-session history;
- statements filed after the signal date;
- missing next-session opening prices;
- unvalidated corporate actions or delisting outcomes;
- inconsistent market calendars;
- non-finite prices, factors, costs, cash, or NAV values.

A run with an invalid week does not present a continuous official performance result. The UI may show diagnostics for the valid prefix but labels the run incomplete.

## Persistence and Reproducibility

Source data remains separate from simulation output. Existing `market.db` and `dart.db` are read or extended only through their storage interfaces. Backtest runs are written to `data/backtest.db`.

The logical storage model includes:

- dated market snapshots;
- prices and reference indices;
- annual filings and their availability dates;
- corporate actions and validation status;
- run configuration and software version;
- source-data refresh timestamps and deterministic data fingerprints;
- daily NAV and benchmark values;
- weekly universe, ranks, exclusions, and holdings;
- requested and executed trades with cost breakdowns.

The same source fingerprints and configuration must reproduce the same result. Each result displays its run identifier and source-data version.

## Verification

Deterministic unit fixtures and integration tests must prove that:

- a statement filed after a signal date is never selected;
- a signal close is never used as the resulting execution price;
- orders execute only at the next valid session's open;
- future listings are excluded and historical delistings remain represented;
- fewer than 80 factor-ready candidates invalidates the week;
- sells fund buys and cash never becomes negative;
- whole-share and minimum-order constraints are respected;
- strategy and primary benchmark use matching timing and cost assumptions;
- repeated runs over identical fingerprints and settings are identical;
- NAV changes reconcile exactly to positions, prices, trades, costs, and external cash flows;
- intentionally introduced look-ahead data is rejected;
- missing execution prices and ambiguous corporate actions cannot silently disappear.

Streamlit tests cover empty data, incomplete readiness, successful execution, result navigation, and audit-detail rendering.

## Delivery Boundaries

The first implementation delivers the reusable engine, persistence schema, deterministic fixtures, readiness diagnostics, and Backtest UI. It must work end to end on test fixtures before being treated as ready for real data.

Historical KRX snapshot acquisition, a full three-year production dataset, dividend ingestion, quarterly or TTM financials, year-specific tax schedules, parameter optimization, and ten-year data acquisition are subsequent increments. Those increments must satisfy this engine's point-in-time contracts rather than bypass them.

## Success Criteria

The engine is ready for the three-year data phase when all automated tests pass, every simulated decision can be traced to source rows available at that time, invalid data produces an explicit failure, and a fixture run reconciles from initial KRW 10,000,000 cash through final NAV with no unexplained difference.
