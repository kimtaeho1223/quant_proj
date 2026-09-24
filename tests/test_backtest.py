import json
import unittest

import pandas as pd

from quantdesk.backtest import BacktestConfig, run_backtest
from backtest_fixtures import three_week_fixture


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

        self.assertEqual(result['engine_version'], '0.4.0')

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


if __name__ == '__main__':
    unittest.main()
