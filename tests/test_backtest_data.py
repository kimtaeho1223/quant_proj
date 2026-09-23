import unittest

import pandas as pd

from quantdesk.backtest_data import BacktestDataError, BacktestDataset, rank_week
from backtest_fixtures import dataset_fixture, listing_fixture, rank_ready_dataset_fixture


class BacktestDatasetTests(unittest.TestCase):
    def test_schedule_uses_last_session_of_holiday_week_and_next_session(self):
        data = dataset_fixture(
            calendar=['2026-09-21', '2026-09-22', '2026-09-24', '2026-09-28'],
            snapshot_date='2026-09-24',
        )

        schedule = data.schedule('2026-09-21', '2026-09-28')

        self.assertEqual(schedule.iloc[0].signal_date, '2026-09-24')
        self.assertEqual(schedule.iloc[0].execution_date, '2026-09-28')

    def test_week_rejects_snapshot_after_signal_date(self):
        data = dataset_fixture(snapshot_date='2026-09-25')

        with self.assertRaisesRegex(BacktestDataError, '미래 종목군'):
            data.week_inputs('2026-09-24')

    def test_week_inputs_exclude_filings_after_signal_date(self):
        data = dataset_fixture(filing_date='2026-09-26')

        week = data.week_inputs('2026-09-25')

        self.assertEqual(week['eligible_statements'], [])

    def test_fingerprint_is_independent_of_input_row_order(self):
        left = dataset_fixture(shuffle=False).fingerprint()
        right = dataset_fixture(shuffle=True).fingerprint()

        self.assertEqual(left, right)

    def test_duplicate_calendar_session_is_rejected(self):
        with self.assertRaisesRegex(BacktestDataError, '거래일 중복'):
            dataset_fixture(calendar=['2026-09-25', '2026-09-25', '2026-09-28'])

    def test_snapshot_missing_required_column_is_rejected(self):
        listing = listing_fixture().drop(columns=['Marcap'])

        with self.assertRaisesRegex(BacktestDataError, '종목군 필수 열'):
            BacktestDataset(
                calendar=['2026-09-25', '2026-09-28'],
                snapshots={'2026-09-25': listing},
                prices={}, statements=[], companies={}, indices={},
                corporate_actions=pd.DataFrame(),
            )

    def test_schedule_skips_week_without_next_execution_session(self):
        data = dataset_fixture(
            calendar=['2026-09-21', '2026-09-22', '2026-09-24'],
            snapshot_date='2026-09-24',
        )

        self.assertTrue(data.schedule('2026-09-21', '2026-09-24').empty)

    def test_rank_week_requires_eighty_factor_ready_candidates(self):
        data = rank_ready_dataset_fixture(candidate_count=79)

        week = rank_week(data, '2026-09-25', minimum_ready=80)

        self.assertIn('유효 후보 79/100', week['issues'])


if __name__ == '__main__':
    unittest.main()
