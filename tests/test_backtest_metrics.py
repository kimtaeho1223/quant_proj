import unittest

import pandas as pd

from quantdesk.backtest_metrics import MetricError, build_comparison, performance_metrics


class BacktestMetricTests(unittest.TestCase):
    def test_max_drawdown_and_price_return_metrics(self):
        nav = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-01', '2026-01-02', '2026-01-05']),
            'equity': [100.0, 80.0, 120.0],
        })

        metrics = performance_metrics(nav, risk_free_rate=0.0)

        self.assertAlmostEqual(metrics['cumulative_return'], 0.20)
        self.assertAlmostEqual(metrics['max_drawdown'], -0.20)
        self.assertEqual(metrics['return_type'], 'price')
        self.assertEqual(metrics['risk_free_rate'], 0.0)

    def test_duplicate_nav_dates_are_rejected(self):
        nav = pd.DataFrame({'date': ['2026-01-01', '2026-01-01'], 'equity': [100, 101]})

        with self.assertRaisesRegex(MetricError, '중복'):
            performance_metrics(nav)

    def test_comparison_uses_only_index_values_available_by_nav_date(self):
        strategy = pd.DataFrame({'date': ['2026-01-01', '2026-01-02'], 'equity': [100, 101]})
        benchmark = pd.DataFrame({'date': ['2026-01-01', '2026-01-02'], 'equity': [100, 102]})
        kospi = pd.DataFrame({'Date': ['2026-01-01', '2026-01-03'], 'Close': [1_000, 2_000]})
        kosdaq = pd.DataFrame({'Date': ['2026-01-01'], 'Close': [500]})

        comparison = build_comparison(strategy, benchmark, kospi, kosdaq)

        self.assertEqual(comparison.loc[1, 'kospi'], 0.0)
        self.assertAlmostEqual(comparison.loc[1, 'strategy'], 0.01)
        self.assertAlmostEqual(comparison.loc[1, 'top100_equal_weight'], 0.02)


if __name__ == '__main__':
    unittest.main()
