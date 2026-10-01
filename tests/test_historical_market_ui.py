import os
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from quantdesk.historical_market_store import HistoricalMarketStore


class HistoricalMarketUiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        os.environ['QUANTDESK_MARKET_DB'] = str(Path(self.folder.name) / 'market.db')
        os.environ['QUANTDESK_RAW_ROOT'] = str(Path(self.folder.name) / 'raw')
        fixture = Path(__file__).with_name('historical_market_ui_fixture_app.py')
        self.app = AppTest.from_file(str(fixture)).run(timeout=30)

    def tearDown(self):
        os.environ.pop('QUANTDESK_MARKET_DB', None)
        os.environ.pop('QUANTDESK_RAW_ROOT', None)
        self.folder.cleanup()

    def test_start_requires_session_key_and_creates_persisted_job(self):
        self.assertEqual(len(self.app.exception), 0)
        self.assertTrue(self.app.button(key='historical_start').disabled)

        self.app.text_input(key='historical_api_key').set_value('session-secret').run()
        self.assertFalse(self.app.button(key='historical_start').disabled)
        self.app.button(key='historical_start').click().run()

        store = HistoricalMarketStore(os.environ['QUANTDESK_MARKET_DB'])
        latest = store.latest_job()
        self.assertIsNotNone(latest)
        self.assertEqual(self.app.session_state['launched_job'], latest['id'])
        self.assertEqual(self.app.session_state['launch_key_length'], len('session-secret'))
        self.assertNotIn('session-secret', str(latest))

    def test_job_status_distinguishes_collection_from_backtest_readiness(self):
        self.app.text_input(key='historical_api_key').set_value('session-secret').run()
        self.app.button(key='historical_start').click().run()

        self.assertTrue(any('공식 백테스트 사용 불가' in item.value for item in self.app.warning))
        self.assertTrue(any(metric.label == '남은 날짜' for metric in self.app.metric))
        self.app.button(key='historical_pause').click().run()
        store = HistoricalMarketStore(os.environ['QUANTDESK_MARKET_DB'])
        self.assertTrue(store.pause_requested(store.latest_job()['id']))


if __name__ == '__main__':
    unittest.main()
