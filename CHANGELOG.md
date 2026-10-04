# Changelog

## Unreleased

- Archive KRX single-security management designation/release history separately from daily status.
- Reconstruct event-derived status only on observed dates and compare disclosure dates in the audit UI.
- Keep official-backtest readiness locked; event history alone does not establish full-market coverage or publication time.

## 0.7.0 - 2026-10-04

- Archive versioned official KRX investability status and require evidence-backed revision selection.
- Apply `investability-v1` to point-in-time signal and execution decisions with explicit unknown states.
- Reconcile status against lifecycle and daily-market evidence; expose audit findings and readiness in Streamlit.
- Keep corporate-action and official-backtest locks closed pending their own validation and representative KRX audits.

## 0.6.0 - 2026-10-03

- Archive versioned official KRX security-master, listing, delisting, and identifier-change evidence.
- Build deterministic issuer, security, event, identifier, name, and listed-membership intervals.
- Require evidence-backed source revision selection and verify rebuild fingerprints before readiness.
- Reconcile lifecycle membership with daily-market rows without inferring delisting from missing data.
- Surface source checksums, timelines, conflicts, and separate readiness locks in Streamlit.
- Keep investability, corporate-action, and official-backtest readiness locked in this increment.

## 0.5.0 - 2026-10-01

- Add official Public Data Portal acquisition for daily full-market KOSPI and KOSDAQ data.
- Archive immutable, SHA-256-addressed raw versions before normalization and validation.
- Persist resumable date states, worker leases, validation findings, and canonical daily rows in SQLite.
- Quarantine changed sources and market-wide anomalies instead of overwriting prior validated data.
- Add a background worker plus Streamlit start, pause, resume, status, and official-CSV recovery workflow.
- Keep historical acquisition isolated from engine-facing snapshots until lifecycle and corporate-action validation is complete.

## 0.4.0 - 2026-09-23

- Add a point-in-time weekly backtest engine with Friday signals and next-session open execution.
- Enforce 252-session warm-up, at least 80 valid candidates per week, and blocking corporate-action checks.
- Add cash-reconciled execution, costs, top-100 equal-weight and KOSPI/KOSDAQ comparisons, and performance metrics.
- Persist source fingerprints, configuration, NAV, trades, weekly holdings, filings, exclusions, and audit evidence.
- Add a readiness-first Streamlit workflow that withholds official metrics from incomplete runs.

## 0.3.4 - 2026-09-20

- Add incremental annual-statement collection that skips symbols already saved for the selected year.
- Add an explicit full-refresh mode and show saved and pending candidate counts before collection.
- Accept official KRX/OpenDART name variants when the stock code and DART corporation number are valid.
- Recover a filing date from a valid receipt number when the filing-list lookup omits that receipt.

## 0.3.3 - 2026-09-20

- Add one-click annual-statement collection for the real ranking's top-100 common-stock universe.
- Prefer consolidated statements and fall back to separate statements only when OpenDART reports no data.
- Persist per-symbol batch results and continue after individual issuer failures.

## 0.3.2 - 2026-09-15

- Reject incomplete KRX listing snapshots so blank market values cannot replace a usable saved universe.
- Fall back to the most recent complete FinanceDataReader KRX cache when the latest listing cache has empty market values.

## 0.3.1 - 2026-09-15

- Add a one-click daily-price refresh for the same top-100 common-stock universe used by the real ranking.
- Preserve per-symbol success or failure logs while continuing the batch after individual provider failures.

## 0.3.0 - 2026-09-14

- Add a real multifactor research ranking for the top 100 KOSPI/KOSDAQ common-stock candidates by cached market capitalization.
- Calculate 12-month momentum and annualized volatility from 252 saved trading days.
- Integrate only matched, filing-date-valid annual DART metrics, preferring consolidated statements for the same year.
- Show data-readiness exclusions instead of assigning ranks from incomplete or mismatched records.

## 0.2.1 - 2026-09-14

- Match DART companies to the cached KRX listing by exact code and whitespace-normalized name.
- Default financial collection to matched companies; unmatched historical records remain available.
- Extract standard annual profit/equity accounts and calculate average-equity ROE.
- Use parent-attributable accounts for consolidated statements; reject missing/ambiguous accounts.
- Show annual earnings/market-cap snapshot ratios only for matched issuers as reference values, not ranks.

## 0.2.0 - 2026-09-14

- Add OpenDART annual financial-statement collection and company lookup.
- Verify filing dates by receipt number and retain distinct correction receipts.
- Keep API keys session-only and sanitize network errors.
- Live authenticated OpenDART collection remains unverified pending a user API key.

## 0.1.1 - 2026-09-14

- Show the most recent attempt per symbol separately from collection history.
- Collapse the latest 500 historical attempts by default; preserve all stored logs.

## 0.1.0 - 2026-09-14

- Streamlit Korean-stock multifactor demo with adjustable weights.
- Twenty-stock portfolio selection, rank-30 retention buffer, cash-aware orders.
- Local holdings, proposed trades, and journal persistence.
- Real-price collection page using FinanceDataReader, KRX listing snapshots,
  NAVER daily prices, SQLite cache, and collection logs.
- PowerShell launcher and VS Code Streamlit debug configuration.
- Twelve automated tests covering core rules, persistence, collection and UI navigation.

### Verification and limitations

Real provider collection and local persistence succeeded for the stock listing and
three symbols. A subsequent refresh from the running app returned connection errors.
The app execution environment's network access remains to be diagnosed; the UI
refresh path must not yet be considered verified end to end.
Cached records survive refresh failure.

Financial statement integration, live strategy ranking, scheduled refresh and
backtesting remain future work. Market databases, holdings, secrets and logs are
excluded from Git. NAVER daily data does not contain actual traded value.
