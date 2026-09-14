import tempfile
import unittest
from pathlib import Path
import pandas as pd
from quantdesk.market import MarketStore, normalize_prices, refresh_prices


def prices():
    return pd.DataFrame(dict(Date=['2026-09-10', '2026-09-11'], Open=[100, 101], High=[110, 111],
                             Low=[90, 91], Close=[105, 106], Volume=[1000, 1100]))


class MarketTests(unittest.TestCase):
    def test_latest_results_keep_only_last_attempt_per_symbol(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MarketStore(Path(folder) / 'market.db')
            store.record('005930', '실패', 'old failure')
            store.record('000660', '실패', 'still failed')
            store.record('005930', '성공', 'recovered')
            latest = store.latest_results()
            self.assertEqual(latest['종목코드'].tolist(), ['005930', '000660'])
            self.assertEqual(latest['상태'].tolist(), ['성공', '실패'])
            self.assertEqual(len(store.logs()), 3)

    def test_invalid_prices_rejected(self):
        frame = prices()
        frame.loc[0, 'Close'] = -1
        with self.assertRaises(ValueError):
            normalize_prices(frame, '2026-09-11')

    def test_future_rows_excluded(self):
        self.assertEqual(len(normalize_prices(prices(), '2026-09-10')), 1)

    def test_refresh_idempotent_and_failure_keeps_data(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MarketStore(Path(folder) / 'market.db')
            provider = lambda *args: prices()
            refresh_prices(store, ['005930'], '2026-09-01', '2026-09-11', provider=provider)
            refresh_prices(store, ['005930'], '2026-09-01', '2026-09-11', provider=provider)
            self.assertEqual(len(store.prices('005930')), 2)
            def failed(*args):
                raise ValueError('upstream unavailable')
            result = refresh_prices(store, ['005930'], '2026-09-01', '2026-09-11', provider=failed)
            self.assertEqual(result[0]['status'], '실패')
            self.assertEqual(len(store.prices('005930')), 2)
            self.assertEqual(len(store.logs()), 3)

    def test_empty_data_not_success(self):
        with self.assertRaises(ValueError):
            normalize_prices(prices().iloc[:0], '2026-09-11')
