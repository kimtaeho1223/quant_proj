# Point-in-Time Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible weekly point-in-time backtest engine and Streamlit result view for the fixed Quant Desk KR multifactor strategy.

**Architecture:** Add a validated point-in-time data contract, separate target selection from next-session execution, and run a deterministic weekly simulation that emits daily NAV and an audit ledger. Persist source-versioned runs in a dedicated SQLite store and expose readiness, performance, trades, and weekly evidence through a new Streamlit page.

**Tech Stack:** Python 3, pandas 2.x, SQLite, Streamlit 1.51+, standard-library `unittest`

**Spec:** `docs/superpowers/specs/2026-09-23-point-in-time-backtest-engine-design.md`

## Global Constraints

- Initial capital is exactly KRW 10,000,000.
- Signal data ends at each week's final trading-day close; execution uses only the next trading session's open.
- The universe is the historical top 100 eligible KOSPI/KOSDAQ listings, never today's listing projected backward.
- Annual statements only; a filing or correction is eligible from its own filing date.
- Fixed factors: momentum 45%, value 20%, quality 20%, low volatility 15%.
- Target 20 equal-weight positions; retain existing positions ranked 30 or better.
- Whole shares, KRW 100,000 minimum order, and 0.3% aggregate cost per side.
- At least 80 top-100 candidates must have valid factor inputs or the week is invalid.
- Cash dividends are excluded and all reported returns are labeled price returns.
- No factor optimization, brokerage orders, quarterly statements, or silent data imputation.
- Source data and simulation output stay separate; results live in `data/backtest.db`.

## Review Focus

- Exchange holiday weeks: select the actual final session and its next distinct session, never a calendar Friday/Monday assumption; Task 1 tests this.
- Suspended holdings: carry the last close for valuation only, and never use it for signals or execution; Task 3 tests this.
- A corrected filing with a later receipt date: keep the original in earlier weeks and switch only after correction availability; Task 2 tests this.
- Expensive shares and minimum-order interactions: preserve non-negative cash and log unfilled target positions deterministically; Task 3 tests this.
- A missing or ambiguous corporate action in the middle of a run: invalidate the official result instead of returning a continuous performance curve; Task 4 tests this.

---

## File Map

- Create `quantdesk/backtest_data.py`: validated in-memory point-in-time dataset, trading schedule, weekly snapshots, readiness diagnostics, and data fingerprints.
- Create `quantdesk/backtest_execution.py`: target selection, whole-share rebalance execution, valuation, and cost ledger.
- Create `quantdesk/backtest.py`: weekly orchestration, primary benchmark simulation, audit records, and run result.
- Create `quantdesk/backtest_metrics.py`: deterministic return and risk metrics.
- Create `quantdesk/backtest_storage.py`: `backtest.db` schema and run persistence.
- Create `quantdesk/backtest_ui.py`: Streamlit readiness, run, summary, trades, and audit views.
- Modify `quantdesk/core.py`: expose the existing rank-retention policy as a reusable pure function while preserving current behavior.
- Modify `quantdesk/market.py`: add read/write interfaces for historical listing snapshots, index prices, and corporate-action validation records; no network collector in this increment.
- Modify `app.py`: add the Backtest navigation entry and renderer.
- Create `tests/backtest_fixtures.py`: deterministic calendars, listings, prices, filings, indices, and expected ledgers shared by backtest tests.
- Create `tests/test_backtest_data.py`, `tests/test_backtest_execution.py`, `tests/test_backtest.py`, `tests/test_backtest_metrics.py`, `tests/test_backtest_storage.py`, and `tests/test_backtest_ui.py`.
- Modify `tests/test_core.py`, `tests/test_market.py`, `tests/test_app.py`, `README.md`, and `CHANGELOG.md` for regression coverage and user-facing limitations.

### Task 1: Point-in-Time Data Contract and Trading Schedule

**Files:**
- Create: `quantdesk/backtest_data.py`
- Create: `tests/backtest_fixtures.py`
- Create: `tests/test_backtest_data.py`

**Interfaces:**
- Produces: `BacktestDataset(calendar, snapshots, prices, statements, companies, indices, corporate_actions)`
- Produces: `BacktestDataset.schedule(start: str, end: str) -> pandas.DataFrame`
- Produces: `BacktestDataset.week_inputs(signal_date: str) -> dict`
- Produces: `BacktestDataset.fingerprint() -> str`
- A raw week-input dictionary contains `signal_date`, `execution_date`, `listing`, `prices_by_code`, `eligible_statements`, `execution_open`, `valuation_closes`, `corporate_actions`, and `issues`.

- [ ] **Step 1: Write schedule and look-ahead rejection tests**

```python
import unittest
import pandas as pd
from quantdesk.backtest_data import BacktestDataError
from tests.backtest_fixtures import dataset_fixture


class BacktestDatasetTest(unittest.TestCase):
    def test_schedule_uses_last_session_of_holiday_week_and_next_session(self):
        data = dataset_fixture(calendar=['2026-09-21', '2026-09-22', '2026-09-24', '2026-09-28'])
        schedule = data.schedule('2026-09-21', '2026-09-28')
        self.assertEqual(schedule.iloc[0].signal_date, '2026-09-24')
        self.assertEqual(schedule.iloc[0].execution_date, '2026-09-28')

    def test_week_rejects_snapshot_after_signal_date(self):
        data = dataset_fixture(snapshot_date='2026-09-25')
        with self.assertRaisesRegex(BacktestDataError, '미래 종목군'):
            data.week_inputs('2026-09-24')
```

- [ ] **Step 2: Run the focused tests and verify the import failure**

Run: `python -m unittest tests.test_backtest_data.BacktestDatasetTest -v`

Expected: `ModuleNotFoundError: No module named 'quantdesk.backtest_data'`.

- [ ] **Step 3: Implement the immutable data contract and exchange-session schedule**

```python
class BacktestDataError(ValueError):
    pass


class BacktestDataset:
    def __init__(self, calendar, snapshots, prices, statements, companies,
                 indices, corporate_actions):
        self.calendar = normalize_calendar(calendar)
        self.snapshots = snapshots
        self.prices = prices
        self.statements = statements
        self.companies = companies
        self.indices = indices
        self.corporate_actions = corporate_actions

    def schedule(self, start, end):
        sessions = self.calendar.loc[
            self.calendar.date.between(start, end), 'date'
        ].sort_values()
        signals = sessions.groupby(pd.to_datetime(sessions).dt.to_period('W-FRI')).last()
        rows = []
        all_sessions = self.calendar.date.sort_values().tolist()
        for signal in signals:
            later = [day for day in all_sessions if day > signal]
            if later:
                rows.append({'signal_date': signal, 'execution_date': later[0]})
        return pd.DataFrame(rows)
```

Implement strict ISO-date normalization, duplicate rejection, and required-column checks. Add reusable deterministic builders to `tests/backtest_fixtures.py`, outside the product package. `week_inputs()` must use the snapshot effective on exactly the signal session, slice prices and filings to records available by that date, and return explicit issues instead of partial success. It does not calculate factor ranks; Task 2 owns that operation.

- [ ] **Step 4: Add filing-availability, required-column, and deterministic-fingerprint tests**

```python
def test_week_inputs_exclude_filings_after_signal_date(self):
    data = dataset_fixture(filing_date='2026-09-26')
    week = data.week_inputs('2026-09-25')
    self.assertEqual(week['eligible_statements'], [])

def test_fingerprint_is_independent_of_input_row_order(self):
    left = dataset_fixture(shuffle=False).fingerprint()
    right = dataset_fixture(shuffle=True).fingerprint()
    self.assertEqual(left, right)
```

- [ ] **Step 5: Run the data-contract tests**

Run: `python -m unittest tests.test_backtest_data -v`

Expected: all tests pass, including duplicate dates, a missing execution session, a future snapshot, a future filing, missing required columns, and stable fingerprints.

- [ ] **Step 6: Commit Task 1**

```powershell
git add quantdesk/backtest_data.py tests/backtest_fixtures.py tests/test_backtest_data.py
git commit -m "feat: add point-in-time backtest data contract"
```

### Task 2: Historical Ranking and Reusable Target Selection

**Files:**
- Modify: `quantdesk/core.py`
- Modify: `quantdesk/real_ranking.py`
- Modify: `tests/test_core.py`
- Modify: `tests/test_real_ranking.py`
- Modify: `tests/test_backtest_data.py`

**Interfaces:**
- Produces: `select_targets(ranked: pandas.DataFrame, held_codes: collection, target_count: int = 20, retention_rank: int = 30) -> list[str]`
- Produces: `build_ranking(..., as_of, weights=None, minimum_history=252) -> tuple[pandas.DataFrame, pandas.DataFrame]`
- Produces: `rank_week(dataset: BacktestDataset, signal_date: str, minimum_ready: int = 80) -> dict` in `backtest_data.py`.
- A ranked week contains all raw inputs plus `ranked`, `exclusions`, and blocking `issues`.

- [ ] **Step 1: Add failing tests for target selection and historical filing corrections**

```python
def test_select_targets_retains_held_name_ranked_thirty(self):
    ranked = pd.DataFrame({'code': [f'{n:06d}' for n in range(1, 32)],
                           'rank': list(range(1, 32))})
    targets = select_targets(ranked, {'000030'}, target_count=20, retention_rank=30)
    self.assertIn('000030', targets)
    self.assertEqual(len(targets), 20)

def test_corrected_filing_changes_only_signals_after_correction_date(self):
    before = select_statement(self.statements_with_correction(), '000001', '2025-04-10')
    after = select_statement(self.statements_with_correction(), '000001', '2025-05-10')
    self.assertEqual(before['receipt'], '20250301000001')
    self.assertEqual(after['receipt'], '20250501000002')

def test_rank_week_requires_eighty_factor_ready_candidates(self):
    data = dataset_fixture(candidate_count=79)
    week = rank_week(data, '2026-09-25', minimum_ready=80)
    self.assertIn('유효 후보 79/100', week['issues'])
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m unittest tests.test_core tests.test_real_ranking tests.test_backtest_data -v`

Expected: failure because `select_targets` is missing or correction selection is wrong.

- [ ] **Step 3: Extract target selection and preserve live rebalance behavior**

```python
def select_targets(ranked, held_codes, target_count=20, retention_rank=30):
    held = set(held_codes)
    kept = ranked[(ranked['rank'] <= retention_rank) & ranked.code.isin(held)]
    kept = kept.head(target_count).code.tolist()
    fill = [code for code in ranked.code.tolist() if code not in kept]
    return kept + fill[:target_count - len(kept)]
```

Replace the inline target selection in `rebalance()` with this function. Do not alter its public return keys or live-page behavior.

- [ ] **Step 4: Make ranking inputs explicitly historical**

Add `minimum_history=252` to `price_factors()` and `build_ranking()`, ensure `select_statement()` orders corrections by availability date and receipt within the selected year, and ensure value always divides by the supplied historical snapshot's market capitalization. Do not read a current listing inside these functions. Implement `rank_week()` as the only adapter from `BacktestDataset.week_inputs()` to `build_ranking()` and add a blocking issue when fewer than `minimum_ready` rows survive.

- [ ] **Step 5: Run ranking and core regression tests**

Run: `python -m unittest tests.test_core tests.test_real_ranking tests.test_backtest_data -v`

Expected: all tests pass and existing weekly portfolio behavior is unchanged.

- [ ] **Step 6: Commit Task 2**

```powershell
git add quantdesk/core.py quantdesk/real_ranking.py tests/test_core.py tests/test_real_ranking.py tests/test_backtest_data.py
git commit -m "refactor: separate historical targets from execution"
```

### Task 3: Whole-Share Execution and Daily Valuation

**Files:**
- Create: `quantdesk/backtest_execution.py`
- Create: `tests/test_backtest_execution.py`

**Interfaces:**
- Consumes: `select_targets()` from Task 2.
- Produces: `execute_rebalance(ranked, holdings, cash, open_prices, cost_model, min_trade=100_000) -> dict`
- Produces: `value_portfolio(holdings, cash, close_prices, last_valid_prices) -> dict`
- Cost model shape: `{'fee_rate': float, 'tax_rate': float, 'slippage_rate': float}`.
- Execution result contains `targets`, `holdings_after`, `cash_after`, `orders`, `unfilled`, `costs`, and `turnover_amount`.

- [ ] **Step 1: Write failing execution-ledger tests**

```python
def test_execution_uses_open_prices_and_never_signal_closes(self):
    ranked = ranked_fixture(signal_close=9_000)
    result = execute_rebalance(ranked, {}, 10_000_000,
        {'000001': 10_000}, default_cost_model())
    self.assertEqual(result['orders'][0]['price'], 10_000)

def test_expensive_target_is_unfilled_without_negative_cash(self):
    result = execute_rebalance(ranked_fixture(), {}, 10_000_000,
        {'000001': 600_000}, default_cost_model())
    self.assertGreaterEqual(result['cash_after'], 0)
    self.assertEqual(result['unfilled'][0]['reason'], '목표 배정액으로 1주 매수 불가')
```

- [ ] **Step 2: Run the tests and verify the missing-module failure**

Run: `python -m unittest tests.test_backtest_execution -v`

Expected: `ModuleNotFoundError: No module named 'quantdesk.backtest_execution'`.

- [ ] **Step 3: Implement deterministic sell-before-buy execution**

```python
def trade_cost(amount, side, model):
    fee = amount * model['fee_rate']
    tax = amount * model['tax_rate'] if side == '매도' else 0.0
    slippage = amount * model['slippage_rate']
    return {'fee': fee, 'tax': tax, 'slippage': slippage,
            'total': fee + tax + slippage}
```

Calculate equal target allocations from pre-trade equity at execution opens, sell first, buy only affordable whole shares, skip orders under KRW 100,000, and preserve deterministic order by target rank then stock code. Do not fall back to rank-table closes when an open is missing.

- [ ] **Step 4: Implement valuation-only last-close carry-forward**

```python
def value_portfolio(holdings, cash, close_prices, last_valid_prices):
    used = {}
    for code, quantity in holdings.items():
        price = close_prices.get(code, last_valid_prices.get(code))
        if price is None:
            raise BacktestExecutionError(f'{code}: 평가 종가 없음')
        used[code] = float(price)
    equity = float(cash + sum(used[c] * q for c, q in holdings.items()))
    return {'equity': equity, 'prices': used}
```

Record whether each price is current or carried. Never return a carried price through the execution interface.

- [ ] **Step 5: Add and run tests for sell funding, minimum orders, cost breakdown, suspensions, and missing opens**

Run: `python -m unittest tests.test_backtest_execution -v`

Expected: all execution tests pass and every ledger amount reconciles to `cash_before - buys - buy_costs + sells - sell_costs`.

- [ ] **Step 6: Commit Task 3**

```powershell
git add quantdesk/backtest_execution.py tests/test_backtest_execution.py
git commit -m "feat: add auditable backtest execution ledger"
```

### Task 4: Weekly Simulation and Audit Trail

**Files:**
- Create: `quantdesk/backtest.py`
- Create: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `BacktestDataset`, `rank_week()`, `execute_rebalance()`, and `value_portfolio()`.
- Produces: `BacktestConfig(start, end, initial_cash=10_000_000, minimum_ready=80, min_trade=100_000, fee_rate=0.003, tax_rate=0.0, slippage_rate=0.0)`.
- Produces: `run_backtest(dataset: BacktestDataset, config: BacktestConfig, external_cashflows: pandas.DataFrame | None = None) -> dict`.
- Result keys: `status`, `config`, `fingerprint`, `nav`, `weekly`, `trades`, `audit`, `issues`, and `benchmark`.

- [ ] **Step 1: Write an end-to-end fixture test with exact ledger reconciliation**

```python
def test_run_uses_friday_signal_and_monday_open_then_reconciles_nav(self):
    dataset = three_week_fixture()
    result = run_backtest(dataset, BacktestConfig('2026-09-01', '2026-09-30'))
    first = result['trades'].iloc[0]
    self.assertEqual(first.signal_date, '2026-09-04')
    self.assertEqual(first.execution_date, '2026-09-07')
    self.assertEqual(first.price_source, 'open')
    self.assertAlmostEqual(result['nav'].iloc[-1].equity,
        result['nav'].iloc[-1].cash + result['nav'].iloc[-1].positions_value)
```

- [ ] **Step 2: Run the test and verify the missing-module failure**

Run: `python -m unittest tests.test_backtest -v`

Expected: `ModuleNotFoundError: No module named 'quantdesk.backtest'`.

- [ ] **Step 3: Implement configuration validation and the weekly event loop**

```python
@dataclass(frozen=True)
class BacktestConfig:
    start: str
    end: str
    initial_cash: int = 10_000_000
    minimum_ready: int = 80
    min_trade: int = 100_000
    fee_rate: float = 0.003
    tax_rate: float = 0.0
    slippage_rate: float = 0.0
```

For each schedule row, fetch the point-in-time week, stop official calculation on any blocking issue, execute at the execution open, and value every session close until the next execution. Store ranked candidates, exclusions, selected filings, requested targets, trades, unfilled orders, and cost fields in the audit output.

Normalize optional external cash flows to `date`, `amount`, `kind`, and `code`. The first UI passes an empty frame because dividends are excluded, but the NAV ledger records supplied flows separately from investment return so later dividend ingestion does not require an engine-interface change.

- [ ] **Step 4: Enforce corporate-action and run-completeness rules**

```python
if week['blocking_corporate_actions']:
    return incomplete_result(
        state, reason='검증되지 않은 기업행사',
        details=week['blocking_corporate_actions'])
```

An incomplete run may retain its valid diagnostic prefix but must set `status='incomplete'`. It cannot expose CAGR, Sharpe, or a final performance comparison as official metrics.

- [ ] **Step 5: Test missing opens, 79 candidates, corrected filings, external cash-flow reconciliation, deterministic reruns, and ambiguous corporate actions**

Run: `python -m unittest tests.test_backtest -v`

Expected: all tests pass; two identical fixture runs have byte-equivalent normalized result records.

- [ ] **Step 6: Commit Task 4**

```powershell
git add quantdesk/backtest.py tests/test_backtest.py
git commit -m "feat: add weekly point-in-time simulation"
```

### Task 5: Primary Benchmark and Performance Metrics

**Files:**
- Create: `quantdesk/backtest_metrics.py`
- Create: `tests/test_backtest_metrics.py`
- Modify: `quantdesk/backtest.py`
- Modify: `tests/test_backtest.py`

**Interfaces:**
- Produces: `performance_metrics(nav: pandas.DataFrame, risk_free_rate: float = 0.0) -> dict`.
- Produces: `build_comparison(strategy_nav, benchmark_nav, kospi, kosdaq) -> pandas.DataFrame`.
- `run_backtest()` adds `metrics`, `benchmark_metrics`, and `comparison` for complete runs.

- [ ] **Step 1: Write exact metric tests**

```python
def test_max_drawdown_and_price_return_metrics(self):
    nav = pd.DataFrame({'date': pd.to_datetime(['2026-01-01', '2026-01-02', '2026-01-05']),
                        'equity': [100.0, 80.0, 120.0]})
    metrics = performance_metrics(nav, risk_free_rate=0.0)
    self.assertAlmostEqual(metrics['cumulative_return'], 0.20)
    self.assertAlmostEqual(metrics['max_drawdown'], -0.20)
    self.assertEqual(metrics['return_type'], 'price')
```

- [ ] **Step 2: Run the tests and verify the missing-module failure**

Run: `python -m unittest tests.test_backtest_metrics -v`

Expected: `ModuleNotFoundError: No module named 'quantdesk.backtest_metrics'`.

- [ ] **Step 3: Implement daily metrics with explicit conventions**

```python
def performance_metrics(nav, risk_free_rate=0.0):
    series = nav.sort_values('date').set_index('date').equity.astype(float)
    returns = series.pct_change().dropna()
    drawdown = series / series.cummax() - 1
    years = (series.index[-1] - series.index[0]).days / 365.2425
    return {
        'cumulative_return': series.iloc[-1] / series.iloc[0] - 1,
        'cagr': (series.iloc[-1] / series.iloc[0]) ** (1 / years) - 1,
        'max_drawdown': drawdown.min(),
        'annual_volatility': returns.std(ddof=1) * math.sqrt(252),
        'sharpe': ((returns.mean() * 252) - risk_free_rate) /
                  (returns.std(ddof=1) * math.sqrt(252)),
        'risk_free_rate': risk_free_rate,
        'return_type': 'price',
    }
```

Reject fewer than two NAV observations, non-positive equity, duplicate dates, and non-finite results with a descriptive `MetricError`.

- [ ] **Step 4: Simulate the equal-weight top-100 benchmark through the same executor**

In `run_backtest()`, create a second account using the same initial cash, weekly dates, whole shares, minimum order, and cost model. Its targets are all eligible point-in-time universe members rather than the top-20 factor ranks. Keep unallocatable amounts in cash. Align KOSPI and KOSDAQ price-index returns to the strategy NAV dates without forward-looking fills.

- [ ] **Step 5: Test matching benchmark costs, cash, timing, and index alignment**

Run: `python -m unittest tests.test_backtest_metrics tests.test_backtest -v`

Expected: all tests pass and strategy/benchmark first executions share the same execution date and cost rules.

- [ ] **Step 6: Commit Task 5**

```powershell
git add quantdesk/backtest.py quantdesk/backtest_metrics.py tests/test_backtest.py tests/test_backtest_metrics.py
git commit -m "feat: add backtest benchmarks and metrics"
```

### Task 6: Historical Source Interfaces and Run Persistence

**Files:**
- Modify: `quantdesk/market.py`
- Modify: `tests/test_market.py`
- Create: `quantdesk/backtest_storage.py`
- Create: `tests/test_backtest_storage.py`

**Interfaces:**
- Produces: `MarketStore.save_historical_snapshot(effective_date, frame)` and `MarketStore.historical_snapshot(effective_date)`.
- Produces: `MarketStore.save_index_prices(symbol, frame)` and `MarketStore.index_prices(symbol)`.
- Produces: `MarketStore.save_corporate_actions(frame)` and `MarketStore.corporate_actions(start, end)`.
- Produces: `BacktestStore(path).save_run(result) -> int`, `.load_run(run_id) -> dict`, and `.runs() -> pandas.DataFrame`.

- [ ] **Step 1: Write schema and round-trip tests**

```python
def test_historical_snapshot_round_trip_preserves_effective_date(self):
    store = MarketStore(self.market_path)
    store.save_historical_snapshot('2026-09-25', listing_fixture())
    saved = store.historical_snapshot('2026-09-25')
    self.assertEqual(saved.effective_date.unique().tolist(), ['2026-09-25'])

def test_backtest_run_round_trip_preserves_fingerprint_and_audit(self):
    store = BacktestStore(self.backtest_path)
    run_id = store.save_run(result_fixture())
    saved = store.load_run(run_id)
    self.assertEqual(saved['fingerprint'], result_fixture()['fingerprint'])
    self.assertEqual(saved['audit'][0]['signal_date'], '2026-09-04')
```

- [ ] **Step 2: Run storage tests and verify failure**

Run: `python -m unittest tests.test_market tests.test_backtest_storage -v`

Expected: missing methods/module failures.

- [ ] **Step 3: Add normalized historical-source tables to `MarketStore`**

```sql
CREATE TABLE IF NOT EXISTS market_snapshots (
  effective_date TEXT, code TEXT, name TEXT, market TEXT,
  security_type TEXT, marcap REAL, amount REAL,
  PRIMARY KEY(effective_date, code)
);
CREATE TABLE IF NOT EXISTS market_indices (
  symbol TEXT, date TEXT, open REAL, close REAL,
  PRIMARY KEY(symbol, date)
);
CREATE TABLE IF NOT EXISTS corporate_actions (
  code TEXT, effective_date TEXT, action_type TEXT,
  ratio REAL, cash_amount REAL, status TEXT, source TEXT,
  PRIMARY KEY(code, effective_date, action_type)
);
```

Validate full snapshots before replacing one effective date. Require explicit `status='validated'` before the backtest data adapter treats a corporate action as usable.

- [ ] **Step 4: Implement normalized run storage**

Create `backtest_runs`, `backtest_nav`, `backtest_weekly`, and `backtest_trades` tables. Store config and audit metadata as UTF-8 JSON only where their shape is intentionally variable. Insert one run and all child rows in a single transaction so a failure cannot leave a partial run.

- [ ] **Step 5: Test invalid snapshot preservation, atomic failure, and deterministic load order**

Run: `python -m unittest tests.test_market tests.test_backtest_storage -v`

Expected: all tests pass; an invalid replacement leaves the previous dated snapshot untouched.

- [ ] **Step 6: Commit Task 6**

```powershell
git add quantdesk/market.py quantdesk/backtest_storage.py tests/test_market.py tests/test_backtest_storage.py
git commit -m "feat: persist historical inputs and backtest runs"
```

### Task 7: Streamlit Backtest Workflow, Documentation, and Full Verification

**Files:**
- Create: `quantdesk/backtest_ui.py`
- Create: `tests/test_backtest_ui.py`
- Modify: `app.py`
- Modify: `tests/test_app.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: source stores from Task 6, `BacktestDataset`, `run_backtest()`, `performance_metrics()`, and `BacktestStore`.
- Produces: `render_backtest(market_path, dart_path, backtest_path) -> None`.

- [ ] **Step 1: Write failing Streamlit navigation and empty-readiness tests**

```python
def test_backtest_page_explains_missing_historical_inputs(self):
    app = AppTest.from_file('app.py', default_timeout=10)
    app.run()
    app.radio(key='page').set_value('백테스트').run()
    self.assertTrue(any('과거 종목군 스냅샷' in item.value for item in app.warning))
    self.assertTrue(any(button.label == '백테스트 실행' and button.disabled
                        for button in app.button))
```

- [ ] **Step 2: Run UI tests and verify failure**

Run: `python -m unittest tests.test_backtest_ui tests.test_app -v`

Expected: failure because the navigation option and renderer do not exist.

- [ ] **Step 3: Implement readiness-first UI**

Add `백테스트` to the sidebar navigation. Show fixed assumptions, requested performance dates, derived warm-up start, source fingerprints, valid/invalid week counts, and blocking reasons before the run button. Disable execution when any required source class is absent or a week has fewer than 80 ready candidates.

```python
if readiness['blocking']:
    st.warning('실행 전 필요한 데이터가 부족합니다.')
    st.dataframe(readiness['issues'], hide_index=True)
st.button('백테스트 실행', type='primary', disabled=readiness['blocking'])
```

- [ ] **Step 4: Render complete results and audit evidence**

Use tabs named `성과`, `주간 포트폴리오`, `거래 내역`, and `감사 기록`. Display strategy, top-100 benchmark, KOSPI, and KOSDAQ curves; cumulative return, CAGR, MDD, volatility, Sharpe, turnover, and costs; then weekly ranks, holdings, filings, exclusions, trades, and source dates. Every result view must show `배당 제외 가격수익률`, run ID, fingerprint, and the 0% risk-free-rate convention.

- [ ] **Step 5: Add successful fixture UI tests and update documentation**

Test tab labels, price-return warning, disabled incomplete runs, successful metric cards, and one selected week's filing/exclusion evidence. Update README with the engine's limits, warm-up requirement, strict 80-name rule, execution timing, data still needed for a real three-year run, and the standard command:

```powershell
python -m streamlit run app.py --server.address 127.0.0.1
```

Add a CHANGELOG entry for the point-in-time engine foundation.

- [ ] **Step 6: Run the complete test suite**

Run: `python -m unittest discover -s tests -v`

Expected: all existing and new tests pass with no regression in current ranking, collection, or weekly portfolio screens.

- [ ] **Step 7: Run syntax and diff checks**

Run: `python -m compileall -q app.py quantdesk tests`

Expected: exit code 0 and no output.

Run: `git diff --check`

Expected: exit code 0 and no output.

- [ ] **Step 8: Launch and inspect the app**

Run: `python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8506`

Expected: the app starts at `http://127.0.0.1:8506`, existing pages still load, and the Backtest page shows readiness diagnostics rather than fabricated results when historical inputs are absent.

- [ ] **Step 9: Commit Task 7**

```powershell
git add app.py quantdesk/backtest_ui.py tests/test_backtest_ui.py tests/test_app.py README.md CHANGELOG.md
git commit -m "feat: add point-in-time backtest workflow"
```

## Final Review Gate

- [ ] Confirm every complete run has a source fingerprint, fixed configuration, and traceable weekly evidence.
- [ ] Confirm an incomplete run never shows official CAGR, Sharpe, or final excess-return claims.
- [ ] Confirm current live ranking and portfolio proposal tests still pass.
- [ ] Confirm no API key, database, generated CSV, or local run artifact is staged.
- [ ] Request a whole-branch code review focused on look-ahead, survivorship bias, cash reconciliation, benchmark parity, and SQLite migration safety.
