import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.backtest_fixtures import three_week_fixture
from quantdesk.backtest import BacktestConfig
from quantdesk.backtest_ui import readiness_report


class BacktestUiTests(unittest.TestCase):
    def test_readiness_blocks_a_week_with_fewer_than_eighty_candidates(self):
        report = readiness_report(
            three_week_fixture(candidate_count=79),
            BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertTrue(report['blocking'])
        self.assertEqual(report['valid_weeks'], 0)
        self.assertGreater(report['invalid_weeks'], 0)
        self.assertTrue(any('유효 후보 79/100' in issue['내용'] for issue in report['issues']))

    def test_complete_result_shows_metrics_and_weekly_evidence(self):
        fixture_app = Path(__file__).with_name('backtest_ui_fixture_app.py')

        app = AppTest.from_file(str(fixture_app), default_timeout=40).run()

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            [tab.label for tab in app.tabs],
            ['성과', '주간 포트폴리오', '거래 내역', '감사 기록'],
        )
        self.assertTrue(any(metric.label == '누적수익률' for metric in app.metric))
        self.assertTrue(any('배당 제외 가격수익률' in item.value for item in app.warning))
        self.assertTrue(any('공시' in item.value for item in app.subheader))


if __name__ == '__main__':
    unittest.main()
