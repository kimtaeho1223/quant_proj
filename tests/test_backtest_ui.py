import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.backtest_fixtures import FakeInvestabilityAdapter, three_week_fixture
from quantdesk.backtest import BacktestConfig
from quantdesk.backtest_ui import readiness_report


class BacktestUiTests(unittest.TestCase):
    def test_missing_investability_adapter_blocks_readiness(self):
        report = readiness_report(
            three_week_fixture(),
            BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertTrue(report['blocking'])
        self.assertTrue(any(
            '공식 상태 자료가 연결되지 않았습니다' in issue['내용']
            for issue in report['issues']
        ))

    def test_readiness_blocks_a_week_with_fewer_than_eighty_candidates(self):
        dataset = three_week_fixture(candidate_count=79)
        adapter = FakeInvestabilityAdapter(dataset.snapshots['2026-09-04'].Code)
        original_readiness = adapter.readiness

        def ready_for_test(start_date, end_date, policy_id):
            return original_readiness(start_date, end_date, policy_id) | {
                'corporate_action_ready': True,
                'official_backtest_ready': True,
            }

        adapter.readiness = ready_for_test
        dataset.investability_adapter = adapter
        report = readiness_report(
            dataset,
            BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertTrue(report['blocking'])
        self.assertEqual(report['valid_weeks'], 0)
        self.assertGreater(report['invalid_weeks'], 0)
        self.assertTrue(any('유효 후보 79/100' in issue['내용'] for issue in report['issues']))

    def test_complete_result_shows_metrics_and_weekly_evidence(self):
        fixture_app = Path(__file__).with_name('backtest_ui_official_fixture_app.py')

        app = AppTest.from_file(str(fixture_app), default_timeout=40).run()

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            [tab.label for tab in app.tabs],
            ['성과', '주간 포트폴리오', '거래 내역', '감사 기록'],
        )
        self.assertTrue(any(metric.label == '누적수익률' for metric in app.metric))
        self.assertTrue(any('배당 제외 가격수익률' in item.value for item in app.warning))
        self.assertTrue(any('공시' in item.value for item in app.subheader))

    def test_legacy_complete_result_hides_official_metrics(self):
        fixture_app = Path(__file__).with_name('backtest_ui_fixture_app.py')

        app = AppTest.from_file(str(fixture_app), default_timeout=40).run()

        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any('공식 성과' in item.value for item in app.error))
        self.assertFalse(any(metric.label == '누적수익률' for metric in app.metric))

    def test_investability_ready_does_not_clear_corporate_action_or_official_locks(self):
        dataset = three_week_fixture()
        dataset.investability_adapter = FakeInvestabilityAdapter(
            dataset.snapshots['2026-09-04'].Code,
            investability_ready=True,
        )

        report = readiness_report(
            dataset, BacktestConfig('2026-09-01', '2026-09-30'),
        )

        self.assertTrue(report['readiness_labels']['investability'])
        self.assertFalse(report['readiness_labels']['corporate_action'])
        self.assertFalse(report['readiness_labels']['official_backtest'])
        self.assertTrue(report['blocking'])


if __name__ == '__main__':
    unittest.main()
