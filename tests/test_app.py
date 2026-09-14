import os
import tempfile
import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest


class AppTests(unittest.TestCase):
    def test_settings_and_save(self):
        with tempfile.TemporaryDirectory() as folder:
            os.environ['QUANTDESK_DB'] = folder + '/app.db'
            try:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.tabs), 5)
                os.environ['QUANTDESK_MARKET_DB'] = folder + '/market.db'
                app.radio(key='page').set_value('실제 주가 데이터').run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.button(key='refresh_prices').label, '선택 종목 주가 갱신')
                app.radio(key='page').set_value('샘플 전략').run()
                app.button(key='save_decision').click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any('저장' in s.value for s in app.success))
                app.slider(key='weight_momentum').set_value(50).run()
                self.assertTrue(any('100' in s.value for s in app.error))
                self.assertEqual(len(app.exception), 0)
            finally:
                os.environ.pop('QUANTDESK_DB', None)
                os.environ.pop('QUANTDESK_MARKET_DB', None)
