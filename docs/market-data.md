# Live Price Collection

Scope approved: stock listings, daily prices, local persistence and update status.
FinanceDataReader 0.9.202 supplies KRX listing and NAVER daily-price readers.
Source: https://github.com/FinanceData/FinanceDataReader
Provider availability and usage conditions can change; this is a personal-use collector.

Implementation: provider subprocess with 35-second timeout; normalized and validated
data; SQLite cache separated from the demo account; per-symbol success/failure logs;
Streamlit real-data page. Previous good data survives failures. Manual updates only,
with persisted history. Scheduled weekly execution is a later step.

NAVER OHLCV has no actual traded amount field. Close times volume must not be
represented as traded amount. Listing Amount and Marcap are snapshot fields, not
historical series. Do not combine real prices with fictional financial factors.

Verification: unit tests for invalid/empty data, idempotent writes and failed refresh
preservation; AppTest for page access; real provider smoke test with bounded requests.
