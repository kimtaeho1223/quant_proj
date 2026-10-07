import json
import unittest
from decimal import Decimal

import pandas as pd

from quantdesk.backtest import BacktestConfig, _apply_research_splits, run_backtest
from quantdesk.corporate_actions import (
    CorporateActionError, PriceBasisEvidence, ResearchActionContext, SplitEvent,
)
from tests.backtest_fixtures import (
    FakeInvestabilityAdapter, dataset_fixture, three_week_fixture,
)


def raw_evidence(**changes):
    fields = dict(
        code='000001', basis='raw', provider='test-provider',
        retrieved_at='2026-10-06T12:00:00+09:00', source_version='v1',
        fingerprint='sha256:example',
    )
    fields.update(changes)
    return PriceBasisEvidence(**fields)


def split_event(**changes):
    fields = dict(
        code='000001', effective_date='2026-09-25',
        published_at='2026-09-24T16:00:00+09:00', action_type='split',
        ratio=Decimal('2'), status='validated',
        source_fingerprint='sha256:action',
    )
    fields.update(changes)
    return SplitEvent(**fields)


RUN_CONFIG = BacktestConfig('2026-09-01', '2026-09-30')
HELD_CODE = '000100'
BENCHMARK_ONLY_CODE = '000001'
_BASELINE = {}


def baseline_result():
    if 'result' not in _BASELINE:
        _BASELINE['result'] = run_backtest(three_week_fixture(), RUN_CONFIG)
    return _BASELINE['result']


def split_scenario(code=HELD_CODE, effective_date='2026-09-16',
                   published_at='2026-09-15T18:00:00+09:00', action_type='split',
                   ratio=Decimal('2'), drop_event_bar=False):
    """Synthetic raw prices that move by the action ratio at the effective open."""
    dataset = three_week_fixture()
    frame = dataset.prices[code].copy()
    frame[['Open', 'Close']] = frame[['Open', 'Close']].astype(float)
    after = frame.Date >= effective_date
    frame.loc[after, ['Open', 'Close']] = frame.loc[after, ['Open', 'Close']] / float(ratio)
    if drop_event_bar:
        frame = frame[frame.Date != effective_date]
    dataset.prices[code] = frame.reset_index(drop=True)
    dataset.corporate_actions = pd.DataFrame([{
        'code': code, 'effective_date': effective_date,
        'action_type': action_type, 'status': 'validated',
    }])
    context = ResearchActionContext(
        evidence=(raw_evidence(code=code),),
        events=(split_event(
            code=code, effective_date=effective_date, published_at=published_at,
            action_type=action_type, ratio=ratio,
        ),),
    )
    return dataset, context


def strategy_quantity(result, execution_date, code):
    weekly = result['weekly'].set_index('execution_date')
    return json.loads(weekly.loc[execution_date, 'holdings']).get(code, 0)


class ResearchSplitStrategyTests(unittest.TestCase):
    def test_helper_converts_whole_shares_without_cash_and_drops_carried_close(self):
        dataset = dataset_fixture()
        frame = dataset.prices['000001'].copy()
        frame[['Open', 'Close']] = [[100.0, 100.0], [50.0, 50.0], [51.0, 52.0]]
        dataset.prices['000001'] = frame
        context = ResearchActionContext(evidence=(raw_evidence(),), events=(split_event(),))
        applied = set()

        holdings, last_valid, audit = _apply_research_splits(
            {'000001': 10}, {'000001': 100.0}, '2026-09-25', dataset, context, applied,
            fractional=False, portfolio='strategy',
        )

        self.assertEqual(holdings, {'000001': 20})
        self.assertIsInstance(holdings['000001'], int)
        self.assertNotIn('000001', last_valid)
        self.assertEqual(len(audit), 1)
        row = audit[0]
        self.assertEqual((row['old_quantity'], row['new_quantity']), (10, 20))
        self.assertEqual((row['raw_open'], row['raw_close']), (50.0, 50.0))
        self.assertEqual(row['effective_date'], '2026-09-25')
        self.assertEqual(row['portfolio'], 'strategy')
        self.assertTrue(row['event_fingerprint'])
        for key in ('cash_delta', 'fee', 'tax', 'slippage', 'gross_amount'):
            self.assertNotIn(key, row)

        again = _apply_research_splits(
            holdings, last_valid, '2026-09-28', dataset, context, applied,
            fractional=False, portfolio='strategy',
        )
        self.assertEqual(again[0], {'000001': 20})
        self.assertEqual(again[2], [])

    def test_helper_blocks_fractional_whole_share_entitlement(self):
        dataset = dataset_fixture()
        reverse = split_event(action_type='reverse_split', ratio=Decimal('0.5'))
        context = ResearchActionContext(evidence=(raw_evidence(),), events=(reverse,))

        with self.assertRaises(CorporateActionError):
            _apply_research_splits(
                {'000001': 5}, {}, '2026-09-25', dataset, context, set(),
                fractional=False, portfolio='strategy',
            )

    def test_helper_blocks_publication_after_effective_open(self):
        dataset = dataset_fixture()
        late = split_event(published_at='2026-09-25T09:00:01+09:00')
        context = ResearchActionContext(evidence=(raw_evidence(),), events=(late,))

        with self.assertRaises(CorporateActionError):
            _apply_research_splits(
                {'000001': 10}, {}, '2026-09-25', dataset, context, set(),
                fractional=False, portfolio='strategy',
            )

    def test_split_between_rebalances_converts_before_daily_valuation(self):
        baseline = baseline_result()
        dataset, context = split_scenario()

        result = run_backtest(dataset, RUN_CONFIG, research_actions=context)

        before = strategy_quantity(baseline, '2026-09-14', HELD_CODE)
        self.assertGreater(before, 0)
        conversions = [row for row in result['corporate_action_audit']
                       if row['portfolio'] == 'strategy']
        self.assertEqual(len(conversions), 1)
        self.assertEqual(conversions[0]['applied_on'], '2026-09-16')
        self.assertEqual(conversions[0]['old_quantity'], before)
        self.assertEqual(conversions[0]['new_quantity'], before * 2)
        compared = baseline['nav'][baseline['nav'].date <= '2026-09-18']
        research_nav = result['nav'].set_index('date')
        for row in compared.itertuples(index=False):
            self.assertAlmostEqual(research_nav.loc[row.date, 'equity'], row.equity, places=6)
            self.assertAlmostEqual(research_nav.loc[row.date, 'cash'], row.cash, places=6)
        self.assertEqual(research_nav.loc['2026-09-16', 'carried_prices'], 0)
        early = lambda frame: frame[frame.execution_date <= '2026-09-14'].to_dict('records')
        self.assertEqual(early(result['trades']), early(baseline['trades']))
        self.assertFalse(result['trades'].execution_date.isin(['2026-09-16']).any())
        self.assertEqual(result['status'], 'incomplete')
        for key in ('metrics', 'benchmark_metrics', 'comparison'):
            self.assertNotIn(key, result)

    def test_rebalance_date_split_converts_once_before_execution(self):
        dataset, context = split_scenario(
            effective_date='2026-09-14', published_at='2026-09-11T18:00:00+09:00',
        )

        result = run_backtest(dataset, RUN_CONFIG, research_actions=context)

        conversions = [row for row in result['corporate_action_audit']
                       if row['portfolio'] == 'strategy']
        self.assertEqual(len(conversions), 1)
        self.assertEqual(conversions[0]['applied_on'], '2026-09-14')
        self.assertEqual(conversions[0]['old_quantity'],
                         strategy_quantity(baseline_result(), '2026-09-07', HELD_CODE))
        self.assertTrue(result['nav'].date.eq('2026-09-30').any())
        self.assertNotIn('metrics', result)

    def test_blocking_split_inputs_return_no_metrics(self):
        quantity = strategy_quantity(baseline_result(), '2026-09-14', HELD_CODE)
        self.assertEqual(quantity % 2, 1)
        cases = {
            'fractional': dict(action_type='reverse_split', ratio=Decimal('0.5')),
            'missing_bar': dict(drop_event_bar=True),
            'late_publication': dict(published_at='2026-09-16T10:00:00+09:00'),
        }
        for name, changes in cases.items():
            with self.subTest(name):
                dataset, context = split_scenario(**changes)
                result = run_backtest(dataset, RUN_CONFIG, research_actions=context)
                self.assertEqual(result['status'], 'incomplete')
                self.assertTrue(any('기업행사' in issue for issue in result['issues']))
                for key in ('metrics', 'benchmark_metrics', 'comparison'):
                    self.assertNotIn(key, result)
                self.assertFalse(result['nav'].date.gt('2026-09-16').any())


class ResearchSplitBenchmarkTests(unittest.TestCase):
    def test_helper_keeps_exact_decimal_fractional_benchmark_quantity(self):
        dataset = dataset_fixture()
        reverse = split_event(action_type='reverse_split', ratio=Decimal('0.5'))
        context = ResearchActionContext(evidence=(raw_evidence(),), events=(reverse,))

        holdings, _, audit = _apply_research_splits(
            {'000001': 5.0}, {}, '2026-09-25', dataset, context, set(),
            fractional=True, portfolio='benchmark',
        )

        self.assertEqual(holdings, {'000001': 2.5})
        self.assertIsInstance(holdings['000001'], float)
        self.assertIsInstance(audit[0]['new_quantity'], Decimal)
        self.assertEqual(audit[0]['new_quantity'], Decimal('2.5'))

    def test_benchmark_converts_reverse_split_strategy_does_not_hold(self):
        baseline = baseline_result()
        dataset, context = split_scenario(
            code=BENCHMARK_ONLY_CODE, action_type='reverse_split', ratio=Decimal('0.5'),
        )

        result = run_backtest(dataset, RUN_CONFIG, research_actions=context)

        self.assertEqual(
            [row for row in result['corporate_action_audit'] if row['portfolio'] == 'strategy'], [],
        )
        benchmark = result['benchmark']
        rows = benchmark['corporate_action_audit']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['applied_on'], '2026-09-16')
        self.assertIsInstance(rows[0]['old_quantity'], Decimal)
        self.assertEqual(rows[0]['new_quantity'], rows[0]['old_quantity'] * Decimal('0.5'))
        self.assertFalse(benchmark['trades'].execution_date.eq('2026-09-16').any())
        expected = baseline['benchmark']['nav']
        actual = benchmark['nav'].set_index('date')
        for row in expected[expected.date <= '2026-09-18'].itertuples(index=False):
            self.assertAlmostEqual(actual.loc[row.date, 'equity'], row.equity, places=4)
            self.assertAlmostEqual(actual.loc[row.date, 'cash'], row.cash, places=6)

    def test_strategy_and_benchmark_share_event_boundary_without_costs(self):
        baseline = baseline_result()
        dataset, context = split_scenario()

        result = run_backtest(dataset, RUN_CONFIG, research_actions=context)

        strategy = [row for row in result['corporate_action_audit'] if row['portfolio'] == 'strategy']
        benchmark = result['benchmark']['corporate_action_audit']
        self.assertEqual(len(strategy), 1)
        self.assertEqual(len(benchmark), 1)
        for key in ('code', 'effective_date', 'applied_on', 'ratio', 'event_fingerprint',
                    'raw_open', 'raw_close'):
            self.assertEqual(strategy[0][key], benchmark[0][key])
        early = lambda frame: frame[frame.execution_date <= '2026-09-14']
        pd.testing.assert_frame_equal(
            early(result['benchmark']['weekly']).reset_index(drop=True),
            early(baseline['benchmark']['weekly']).reset_index(drop=True),
        )
        expected = baseline['benchmark']['nav']
        actual = result['benchmark']['nav'].set_index('date')
        for row in expected[expected.date <= '2026-09-18'].itertuples(index=False):
            self.assertAlmostEqual(actual.loc[row.date, 'equity'], row.equity, places=4)

    def test_rebalance_date_event_is_applied_once_to_benchmark(self):
        dataset, context = split_scenario(
            effective_date='2026-09-14', published_at='2026-09-11T18:00:00+09:00',
        )

        result = run_backtest(dataset, RUN_CONFIG, research_actions=context)

        rows = result['benchmark']['corporate_action_audit']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['applied_on'], '2026-09-14')

    def test_completed_research_run_is_locked_and_reproducible(self):
        dataset, context = split_scenario()

        first = run_backtest(dataset, RUN_CONFIG, research_actions=context)
        second = run_backtest(dataset, RUN_CONFIG, research_actions=context)

        self.assertEqual(first['status'], 'incomplete')
        self.assertIn('기업행사 연구용 계산 결과이며 공식 성과가 아닙니다.', first['issues'])
        self.assertFalse(any('연결되지 않았습니다' in issue for issue in first['issues']))
        for key in ('metrics', 'benchmark_metrics', 'comparison'):
            self.assertNotIn(key, first)
        self.assertTrue(first['nav'].date.eq('2026-09-30').any())
        self.assertTrue(first['benchmark']['nav'].date.eq('2026-09-30').any())
        self.assertNotEqual(first['fingerprint'], dataset.fingerprint())
        self.assertEqual(first['research_action_context_fingerprint'], context.fingerprint())

        def normalized(result):
            return json.dumps({
                'fingerprint': result['fingerprint'],
                'audit': result['audit'],
                'actions': result['corporate_action_audit'],
                'benchmark_actions': result['benchmark']['corporate_action_audit'],
                'nav': result['nav'].to_dict('records'),
                'benchmark_nav': result['benchmark']['nav'].to_dict('records'),
            }, sort_keys=True, ensure_ascii=False, default=str)

        self.assertEqual(normalized(first), normalized(second))

    def test_benchmark_block_preserves_diagnostics_without_metrics(self):
        dataset, context = split_scenario(
            code=BENCHMARK_ONLY_CODE, action_type='reverse_split', ratio=Decimal('0.5'),
            drop_event_bar=True,
        )

        result = run_backtest(dataset, RUN_CONFIG, research_actions=context)

        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any('벤치마크' in issue and '기업행사' in issue for issue in result['issues']))
        self.assertTrue(result['nav'].date.eq('2026-09-30').any())
        self.assertEqual(result['benchmark']['status'], 'incomplete')
        for key in ('metrics', 'benchmark_metrics', 'comparison'):
            self.assertNotIn(key, result)

    def test_research_run_does_not_clear_investability_locks(self):
        dataset, context = split_scenario()
        dataset.investability_adapter = FakeInvestabilityAdapter(
            dataset.snapshots['2026-09-04'].Code, investability_ready=True,
        )

        result = run_backtest(dataset, RUN_CONFIG, research_actions=context)

        self.assertEqual(result['status'], 'incomplete')
        self.assertFalse(result['investability_readiness']['corporate_action_ready'])
        self.assertFalse(result['investability_readiness']['official_backtest_ready'])
        for key in ('metrics', 'benchmark_metrics', 'comparison'):
            self.assertNotIn(key, result)

    def test_same_split_without_context_still_blocks(self):
        dataset, _ = split_scenario()

        result = run_backtest(dataset, RUN_CONFIG)

        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any('계산에 반영되지 않은 기업행사' in issue for issue in result['issues']))
        self.assertTrue(result['nav'].empty or len(result['nav']) == 0)
        self.assertNotIn('metrics', result)


class BacktestTests(unittest.TestCase):
    def test_run_uses_friday_signal_and_monday_open_then_reconciles_nav(self):
        result = run_backtest(
            three_week_fixture(), BacktestConfig('2026-09-01', '2026-09-30'),
        )

        first = result['trades'].iloc[0]
        self.assertEqual(first.signal_date, '2026-09-04')
        self.assertEqual(first.execution_date, '2026-09-07')
        self.assertEqual(first.price_source, 'open')
        final = result['nav'].iloc[-1]
        self.assertAlmostEqual(final.equity, final.cash + final.positions_value)
        self.assertEqual(result['status'], 'complete')

    def test_missing_open_is_logged_without_using_a_close(self):
        result = run_backtest(
            three_week_fixture(missing_open_date='2026-09-07'),
            BacktestConfig('2026-09-01', '2026-09-30'),
        )

        unfilled = [item for week in result['audit'] for item in week['unfilled']]
        self.assertTrue(any(item['reason'] == '다음 거래일 시가 없음' for item in unfilled))
        self.assertTrue(all(trade['price_source'] == 'open' for trade in result['trades'].to_dict('records')))

    def test_seventy_nine_candidates_make_run_incomplete(self):
        result = run_backtest(
            three_week_fixture(candidate_count=79),
            BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any('유효 후보 79/100' in issue for issue in result['issues']))

    def test_corrected_filing_is_used_only_after_its_filing_date(self):
        result = run_backtest(
            three_week_fixture(corrected=True),
            BacktestConfig('2026-09-01', '2026-09-30'),
        )

        receipts = {
            week['signal_date']: next(item['receipt'] for item in week['filings'] if item['stock'] == '000001')
            for week in result['audit']
        }
        self.assertTrue(receipts['2026-09-04'].startswith('20260331'))
        self.assertTrue(receipts['2026-09-11'].startswith('20260910'))

    def test_external_cashflow_reconciles_in_daily_nav(self):
        dataset = three_week_fixture()
        config = BacktestConfig('2026-09-01', '2026-09-30')
        baseline = run_backtest(dataset, config)
        flows = pd.DataFrame([{
            'date': '2026-09-30', 'amount': 100_000, 'kind': 'dividend', 'code': '000001',
        }])

        funded = run_backtest(dataset, config, external_cashflows=flows)

        self.assertAlmostEqual(funded['nav'].iloc[-1].equity - baseline['nav'].iloc[-1].equity, 100_000)
        self.assertEqual(funded['nav'].iloc[-1].external_flow, 100_000)

    def test_unvalidated_corporate_action_stops_official_result(self):
        result = run_backtest(
            three_week_fixture(corporate_action_status='unknown'),
            BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any('검증되지 않은 기업행사' in issue for issue in result['issues']))
        self.assertNotIn('metrics', result)

    def test_validated_action_without_accounting_stops_result(self):
        result = run_backtest(
            three_week_fixture(corporate_action_status='validated'),
            BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any('계산에 반영되지 않은 기업행사' in issue for issue in result['issues']))
        self.assertNotIn('metrics', result)

    def test_research_context_must_match_dataset_actions(self):
        config = BacktestConfig('2026-09-01', '2026-09-30')
        dataset = three_week_fixture(corporate_action_status='validated')
        matching = split_event(effective_date='2026-09-15')
        for events in ((), (matching, split_event(code='000002'))):
            with self.subTest(events=events):
                context = ResearchActionContext(evidence=(raw_evidence(),), events=events)
                result = run_backtest(dataset, config, research_actions=context)
                self.assertEqual(result['status'], 'incomplete')
                self.assertNotIn('metrics', result)
                self.assertTrue(any('입력과 데이터셋이 일치하지' in issue for issue in result['issues']))

    def test_research_context_rejects_invalid_dataset_action_date(self):
        dataset = three_week_fixture(corporate_action_status='validated')
        dataset.corporate_actions.loc[0, 'effective_date'] = 'not-a-date'
        context = ResearchActionContext(evidence=(), events=())

        result = run_backtest(
            dataset, BacktestConfig('2026-09-01', '2026-09-30'),
            research_actions=context,
        )

        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any('행사일이 올바르지' in issue for issue in result['issues']))
        self.assertNotIn('metrics', result)

    def test_research_context_rejects_unknown_price_basis(self):
        dataset = three_week_fixture(corporate_action_status='validated')
        context = ResearchActionContext(
            evidence=(raw_evidence(basis='unknown'),),
            events=(split_event(effective_date='2026-09-15'),),
        )
        result = run_backtest(
            dataset, BacktestConfig('2026-09-01', '2026-09-30'),
            research_actions=context,
        )
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('metrics', result)
        self.assertTrue(any('가격 기준' in issue for issue in result['issues']))

    def test_unvalidated_corporate_action_in_warmup_stops_official_result(self):
        dataset = three_week_fixture()
        dataset.corporate_actions = pd.DataFrame([{
            'code': '000001', 'effective_date': '2026-08-20',
            'action_type': 'split', 'status': 'unknown',
        }])

        result = run_backtest(
            dataset, BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any('검증되지 않은 기업행사' in issue for issue in result['issues']))
        self.assertNotIn('metrics', result)

    def test_identical_inputs_produce_identical_normalized_results(self):
        dataset = three_week_fixture()
        config = BacktestConfig('2026-09-01', '2026-09-30')

        first = run_backtest(dataset, config)
        second = run_backtest(dataset, config)

        def normalized(result):
            return json.dumps({
                'status': result['status'],
                'fingerprint': result['fingerprint'],
                'nav': result['nav'].to_dict('records'),
                'weekly': result['weekly'].to_dict('records'),
                'trades': result['trades'].to_dict('records'),
                'audit': result['audit'],
                'issues': result['issues'],
            }, sort_keys=True, ensure_ascii=False, default=str)

        self.assertEqual(normalized(first), normalized(second))

    def test_result_records_the_engine_version_for_reproduction(self):
        result = run_backtest(
            three_week_fixture(), BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertEqual(result['engine_version'], '0.4.1')

    def test_complete_run_includes_fractional_benchmark_with_matching_timing_and_costs(self):
        config = BacktestConfig('2026-09-01', '2026-09-30')

        result = run_backtest(three_week_fixture(), config)

        self.assertIn('metrics', result)
        self.assertIn('benchmark_metrics', result)
        self.assertFalse(result['benchmark']['nav'].empty)
        first = result['benchmark']['trades'].iloc[0]
        self.assertEqual(first.execution_date, result['trades'].iloc[0].execution_date)
        self.assertEqual(result['benchmark']['cost_model']['fee_rate'], config.fee_rate)
        self.assertEqual(result['comparison'].columns.tolist(), [
            'date', 'strategy', 'top100_equal_weight', 'kospi', 'kosdaq',
        ])

    def test_execution_day_suspension_rejects_order_and_preserves_signal_audit(self):
        dataset = three_week_fixture()
        codes = dataset.snapshots['2026-09-04'].Code.tolist()
        adapter = FakeInvestabilityAdapter(
            codes,
            execution_states={
                ('2026-09-07', code): ('ineligible', ('trading_suspension',))
                for code in codes
            },
        )
        dataset.investability_adapter = adapter

        result = run_backtest(
            dataset, BacktestConfig('2026-09-01', '2026-09-30'),
        )

        first = result['audit'][0]
        self.assertEqual(len(first['investability_signal']), 100)
        rejection = first['execution_rejections'][0]
        self.assertEqual(rejection['state'], 'ineligible')
        self.assertIn('trading_suspension', rejection['reason_codes'])
        self.assertIn('signal_timestamp', rejection)
        self.assertIn('execution_timestamp', rejection)
        self.assertFalse(
            result['trades'].execution_date.eq('2026-09-07').any()
        )

    def test_existing_suspended_holding_carries_last_close_for_valuation_only(self):
        baseline_dataset = three_week_fixture()
        baseline = run_backtest(
            baseline_dataset, BacktestConfig('2026-09-01', '2026-09-30'),
        )
        held_code = baseline['trades'].iloc[0].code
        dataset = three_week_fixture()
        dataset.prices[held_code] = dataset.prices[held_code][
            dataset.prices[held_code].Date.ne('2026-09-14')
        ].reset_index(drop=True)
        codes = dataset.snapshots['2026-09-04'].Code.tolist()
        dataset.investability_adapter = FakeInvestabilityAdapter(
            codes,
            execution_states={
                ('2026-09-14', held_code): ('ineligible', ('trading_suspension',)),
            },
        )

        result = run_backtest(
            dataset, BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertTrue(any(
            item['security_id'] == f'sec-{held_code}'
            for item in result['audit'][1]['execution_rejections']
        ))
        self.assertGreater(result['nav'].carried_prices.max(), 0)

    def test_unknown_investability_blocks_official_result(self):
        dataset = three_week_fixture()
        dataset.investability_adapter = FakeInvestabilityAdapter(
            dataset.snapshots['2026-09-04'].Code,
            investability_ready=False,
        )

        result = run_backtest(
            dataset, BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(any('투자 가능성' in issue for issue in result['issues']))
        self.assertNotIn('metrics', result)

    def test_fixture_backtest_result_is_unchanged_without_official_adapter(self):
        result = run_backtest(
            three_week_fixture(), BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['trades'].iloc[0].execution_date, '2026-09-07')
        self.assertNotIn('investability_readiness', result)


if __name__ == '__main__':
    unittest.main()
