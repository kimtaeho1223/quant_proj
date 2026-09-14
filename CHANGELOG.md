# Changelog

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
