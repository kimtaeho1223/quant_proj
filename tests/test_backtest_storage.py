import tempfile
import unittest
from pathlib import Path

import pandas as pd

from quantdesk.backtest_storage import BacktestStore


def result_fixture():
    return {
        'status': 'complete',
        'config': {'start': '2026-09-01', 'end': '2026-09-30'},
        'fingerprint': 'abc123',
        'nav': pd.DataFrame([
            {'date': '2026-09-01', 'cash': 10_000_000, 'positions_value': 0,
             'equity': 10_000_000, 'external_flow': 0, 'carried_prices': 0},
            {'date': '2026-09-02', 'cash': 100_000, 'positions_value': 9_950_000,
             'equity': 10_050_000, 'external_flow': 0, 'carried_prices': 0},
        ]),
        'weekly': pd.DataFrame([{
            'signal_date': '2026-09-04', 'execution_date': '2026-09-07',
            'candidate_count': 100, 'holding_count': 20,
        }]),
        'trades': pd.DataFrame([{
            'signal_date': '2026-09-04', 'execution_date': '2026-09-07',
            'code': '005930', 'side': '매수', 'quantity': 10, 'price': 70_000,
        }]),
        'audit': [{'signal_date': '2026-09-04', 'filings': [{'stock': '005930'}]}],
        'issues': [],
        'benchmark': {'method': 'fractional_equal_weight', 'nav': pd.DataFrame([
            {'date': '2026-09-01', 'equity': 10_000_000},
        ])},
        'metrics': {'cumulative_return': 0.005},
        'benchmark_metrics': {'cumulative_return': 0.003},
        'comparison': pd.DataFrame([{
            'date': '2026-09-01', 'strategy': 0.0,
            'top100_equal_weight': 0.0, 'kospi': 0.0, 'kosdaq': 0.0,
        }]),
    }


class BacktestStorageTests(unittest.TestCase):
    def test_backtest_run_round_trip_preserves_fingerprint_and_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            store = BacktestStore(Path(folder) / 'backtest.db')

            run_id = store.save_run(result_fixture())
            saved = store.load_run(run_id)

            self.assertEqual(saved['fingerprint'], 'abc123')
            self.assertEqual(saved['audit'][0]['signal_date'], '2026-09-04')
            self.assertEqual(saved['nav'].date.tolist(), ['2026-09-01', '2026-09-02'])
            self.assertEqual(saved['benchmark']['nav'].iloc[0].equity, 10_000_000)

    def test_runs_are_listed_newest_first(self):
        with tempfile.TemporaryDirectory() as folder:
            store = BacktestStore(Path(folder) / 'backtest.db')
            first = store.save_run(result_fixture())
            second = store.save_run(result_fixture())

            self.assertEqual(store.runs().id.tolist(), [second, first])

    def test_serialization_failure_does_not_leave_partial_run(self):
        with tempfile.TemporaryDirectory() as folder:
            store = BacktestStore(Path(folder) / 'backtest.db')
            broken = result_fixture()
            broken['audit'] = [{'bad': object()}]

            with self.assertRaises(TypeError):
                store.save_run(broken)

            self.assertTrue(store.runs().empty)


if __name__ == '__main__':
    unittest.main()
