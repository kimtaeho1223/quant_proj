import os
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from streamlit.testing.v1 import AppTest
from quantdesk.dart import DartStore
from quantdesk.market import MarketStore


class AppTests(unittest.TestCase):
    def test_settings_and_save(self):
        with tempfile.TemporaryDirectory() as folder:
            os.environ['QUANTDESK_DB'] = folder + '/app.db'
            os.environ['QUANTDESK_MARKET_DB'] = folder + '/market.db'
            os.environ['QUANTDESK_DART_DB'] = folder + '/dart.db'
            os.environ['QUANTDESK_BACKTEST_DB'] = folder + '/backtest.db'
            try:
                MarketStore(os.environ['QUANTDESK_MARKET_DB']).save_listing(pd.DataFrame([
                    dict(Code='005930', Name='삼성전자', Market='KOSPI', Marcap=1_000, Amount=100),
                ]))
                DartStore(os.environ['QUANTDESK_DART_DB']).save_companies({
                    '005930': {'corp_code': '00126380', 'name': '삼성전자'},
                })
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.tabs), 5)
                app.radio(key='page').set_value('실제 멀티팩터 순위').run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any('실제 멀티팩터 순위' in item.value for item in app.subheader))
                self.assertEqual([tab.label for tab in app.tabs], ['멀티팩터 순위', '주간 포트폴리오', '매매 제안'])
                app.radio(key='page').set_value('재무정보').run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.button(key='refresh_top_statements').label, '상위 100개 재무제표 일괄 수집')
                self.assertEqual(app.radio(key='financial_refresh_mode').value, '부족한 종목만')
                self.assertTrue(any('수집 예정 1' in item.value for item in app.caption))
                app.button(key='refresh_top_statements').click().run()
                self.assertTrue(any('40자리' in item.value for item in app.error))
                app.button(key='dart_companies').click().run()
                self.assertTrue(any('40자리' in item.value for item in app.error))
                self.assertEqual(len(app.exception), 0)
                app.radio(key='page').set_value('실제 주가 데이터').run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.button(key='refresh_prices').label, '선택 종목 주가 갱신')
                self.assertEqual(app.button(key='refresh_top_prices').label, '상위 100개 주가 일괄 수집')
                app.radio(key='page').set_value('백테스트').run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any('과거 종목군 스냅샷' in item.value for item in app.warning))
                self.assertTrue(any(
                    button.label == '백테스트 실행' and button.disabled
                    for button in app.button
                ))
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
                os.environ.pop('QUANTDESK_DART_DB', None)
                os.environ.pop('QUANTDESK_BACKTEST_DB', None)
