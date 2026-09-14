# Real Multifactor Ranking Design

## Goal

Show a current Korean-equity multifactor ranking for the 100 largest eligible KOSPI/KOSDAQ listings using only locally stored prices and verified OpenDART statements.

## Candidate Universe

Use the latest saved KRX listing snapshot. Keep KOSPI/KOSDAQ ordinary common-share candidates with valid six-digit numeric codes, positive market capitalization and positive listing traded value. Exclude names containing preferred-share, SPAC and REIT markers. Sort by market capitalization descending and take the first 100.

## Point-in-Time Rules

The selected as-of date is a calendar date. A price row is eligible only on or before that date. Momentum and volatility use the most recent 252 trading days on or before that date; a stock with fewer than 252 closes is excluded. Financial statements are eligible only when their verified OpenDART filing date is on or before the as-of date. For the same business year, prefer consolidated CFS to separate OFS; then prefer the latest filing date and receipt. Current KRX market capitalization is a snapshot and therefore makes the value ratio a current reference value, not a historical backtest input.

## Factors

- Momentum: 252-trading-day close return, higher is better.
- Low volatility: annualized standard deviation of the preceding daily close returns, lower is better.
- Value: annual annual profit divided by the KRX listing snapshot market capitalization, higher is better.
- Quality: ROE from the verified annual statement, higher is better.

Weights are the existing 45% momentum, 20% value, 20% quality and 15% low-volatility settings. Percentile scoring follows the existing `score_stocks` implementation.

## User Flow

The new actual-ranking page shows the candidate count, data readiness counts, a ranked table, factor details, and excluded candidates with explicit reasons. It does not send orders or alter the demo portfolio. The ranking refreshes from local databases; the user collects missing prices and statements in the existing data pages before rerunning it.

## Safety and Limits

No API key is persisted. Statements that do not exact-match the saved DART company record and listing are excluded. Missing, non-finite, or invalid price and financial values never receive an imputed score. The page labels the result as a current research view; it is not a backtest and does not establish performance.
