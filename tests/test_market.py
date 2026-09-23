import tempfile
import unittest
from pathlib import Path
from datetime import date
import pandas as pd
from quantdesk.market import MarketStore, normalize_prices, refresh_prices, refresh_top_prices
from quantdesk.market_worker import read_listing
from backtest_fixtures import listing_fixture


def prices():
    return pd.DataFrame(dict(Date=['2026-09-10', '2026-09-11'], Open=[100, 101], High=[110, 111],
                             Low=[90, 91], Close=[105, 106], Volume=[1000, 1100]))


class MarketTests(unittest.TestCase):
    def test_historical_snapshot_round_trip_preserves_effective_date(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MarketStore(Path(folder) / 'market.db')

            store.save_historical_snapshot('2026-09-25', listing_fixture())
            saved = store.historical_snapshot('2026-09-25')

            self.assertEqual(saved.effective_date.unique().tolist(), ['2026-09-25'])
            self.assertEqual(len(saved), 100)

    def test_invalid_historical_snapshot_preserves_saved_date(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MarketStore(Path(folder) / 'market.db')
            store.save_historical_snapshot('2026-09-25', listing_fixture())

            with self.assertRaisesRegex(ValueError, '필수 열'):
                store.save_historical_snapshot(
                    '2026-09-25', listing_fixture().drop(columns=['Marcap']),
                )

            self.assertEqual(len(store.historical_snapshot('2026-09-25')), 100)

    def test_index_and_corporate_action_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MarketStore(Path(folder) / 'market.db')
            index = pd.DataFrame([
                {'Date': '2026-09-24', 'Open': 3_000, 'Close': 3_010},
                {'Date': '2026-09-25', 'Open': 3_020, 'Close': 3_030},
            ])
            actions = pd.DataFrame([{
                'code': '005930', 'effective_date': '2026-09-25',
                'action_type': 'split', 'ratio': 2.0, 'cash_amount': None,
                'status': 'validated', 'source': 'KRX',
            }])

            store.save_index_prices('KOSPI', index)
            store.save_corporate_actions(actions)

            self.assertEqual(store.index_prices('KOSPI').Close.tolist(), [3_010, 3_030])
            saved = store.corporate_actions('2026-09-01', '2026-09-30')
            self.assertEqual(saved.status.tolist(), ['validated'])

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

    def test_top_price_refresh_uses_rank_candidates_and_continues_after_failure(self):
        listing = pd.DataFrame([
            dict(Code='000001', Name='알파', Market='KOSPI', Marcap=300, Amount=30),
            dict(Code='000002', Name='알파우', Market='KOSPI', Marcap=400, Amount=40),
            dict(Code='000003', Name='베타', Market='KOSDAQ', Marcap=200, Amount=20),
        ])
        with tempfile.TemporaryDirectory() as folder:
            store = MarketStore(Path(folder) / 'market.db')
            calls = []

            def provider(code, *_):
                calls.append(code)
                if code == '000001':
                    raise ValueError('upstream unavailable')
                return prices()

            result = refresh_top_prices(store, listing, '2026-09-01', '2026-09-11', provider=provider)

            self.assertEqual(calls, ['000001', '000003'])
            self.assertEqual([entry['status'] for entry in result], ['실패', '성공'])
            self.assertEqual(len(store.prices('000003')), 2)

    def test_listing_rejects_empty_market_values_and_preserves_previous_snapshot(self):
        valid = pd.DataFrame([
            dict(Code='000001', Name='알파', Market='KOSPI', Marcap=300, Amount=30),
        ])
        broken = valid.assign(Marcap=float('nan'), Amount=float('nan'))
        with tempfile.TemporaryDirectory() as folder:
            store = MarketStore(Path(folder) / 'market.db')
            store.save_listing(valid)
            with self.assertRaisesRegex(ValueError, '시가총액'):
                store.save_listing(broken)
            saved, _ = store.listing()
            self.assertEqual(saved.Marcap.tolist(), [300])

    def test_worker_uses_recent_complete_cache_when_latest_listing_is_blank(self):
        blank = pd.DataFrame([
            dict(Code='000001', Name='알파', Market='KOSPI', Marcap=None, Amount=None),
        ])
        complete = blank.assign(Marcap=300, Amount=30)
        requested = []

        def read_cache(url, **_):
            requested.append(url)
            return complete if url.endswith('/2026-09-11.csv') else blank

        result = read_listing(lambda _: blank, read_cache=read_cache, today=date(2026, 9, 15))

        self.assertEqual(result.Marcap.tolist(), [300])
        self.assertTrue(requested[-1].endswith('/2026-09-11.csv'))

    def test_worker_uses_recent_complete_cache_when_listing_provider_raises(self):
        complete = pd.DataFrame([
            dict(Code='000001', Name='알파', Market='KOSPI', Marcap=300, Amount=30),
        ])
        requested = []

        def unavailable(_):
            raise ConnectionError('KRX unavailable')

        def read_cache(url, **_):
            requested.append(url)
            return complete

        result = read_listing(unavailable, read_cache=read_cache, today=date(2026, 9, 15))

        self.assertEqual(result.Marcap.tolist(), [300])
        self.assertTrue(requested[0].endswith('/2026-09-14.csv'))
