import unittest
from decimal import Decimal

import pandas as pd

from quantdesk.backtest_data import BacktestDataError, BacktestDataset, rank_week
from quantdesk.corporate_actions import (
    CorporateActionError, PriceBasisEvidence, ResearchActionContext, SplitEvent,
)
from tests.backtest_fixtures import (
    FakeInvestabilityAdapter,
    dataset_fixture,
    listing_fixture,
    rank_ready_dataset_fixture,
)


class BacktestDatasetTests(unittest.TestCase):
    def test_research_signal_history_adjusts_only_close(self):
        data = dataset_fixture()
        data.prices['000001']['Close'] = data.prices['000001'].Close.astype(float)
        data.prices['000001'].loc[:1, 'Close'] = [100.0, 50.0]
        original = data.prices['000001'].copy(deep=True)
        context = ResearchActionContext(
            evidence=(PriceBasisEvidence(
                '000001', 'raw', 'synthetic', '2026-10-06T12:00:00+09:00',
                'v1', 'sha256:prices',
            ),),
            events=(SplitEvent(
                '000001', '2026-09-25', '2026-09-24T16:00:00+09:00',
                'split', Decimal('2'), 'validated', 'sha256:split',
            ),),
        )

        week = data.week_inputs('2026-09-25', research_actions=context)

        self.assertEqual(week['prices_by_code']['000001'].Close.tolist(), [50.0, 50.0])
        self.assertEqual(week['prices_by_code']['000001'].Open.tolist(),
                         original.loc[:1, 'Open'].tolist())
        self.assertEqual(week['execution_open']['000001'], 10_300.0)
        pd.testing.assert_frame_equal(data.prices['000001'], original)

    def test_research_signal_blocks_late_publication(self):
        data = dataset_fixture()
        context = ResearchActionContext(
            evidence=(PriceBasisEvidence(
                '000001', 'raw', 'synthetic', '2026-10-06T12:00:00+09:00',
                'v1', 'sha256:prices',
            ),),
            events=(SplitEvent(
                '000001', '2026-09-25', '2026-09-25T18:00:00+09:00',
                'split', Decimal('2'), 'validated', 'sha256:split',
            ),),
        )
        with self.assertRaises(CorporateActionError):
            data.week_inputs('2026-09-25', research_actions=context)

    def test_research_signal_rejects_missing_price_basis(self):
        data = dataset_fixture()
        context = ResearchActionContext(
            evidence=(),
            events=(SplitEvent(
                '000001', '2026-09-25', '2026-09-24T16:00:00+09:00',
                'split', Decimal('2'), 'validated', 'sha256:split',
            ),),
        )
        with self.assertRaises(CorporateActionError):
            data.week_inputs('2026-09-25', research_actions=context)

    def test_future_split_does_not_adjust_earlier_signal(self):
        data = dataset_fixture()
        context = ResearchActionContext(
            evidence=(PriceBasisEvidence(
                '000001', 'raw', 'synthetic', '2026-10-06T12:00:00+09:00',
                'v1', 'sha256:prices',
            ),),
            events=(SplitEvent(
                '000001', '2026-09-28', '2026-09-25T16:00:00+09:00',
                'split', Decimal('2'), 'validated', 'sha256:split',
            ),),
        )

        week = data.week_inputs('2026-09-25', research_actions=context)

        self.assertEqual(week['prices_by_code']['000001'].Close.tolist(), [10_000, 10_200])

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

    def test_schedule_does_not_treat_midweek_end_as_weekly_signal(self):
        data = dataset_fixture(
            calendar=['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02'],
            snapshot_date='2026-09-30',
        )

        self.assertTrue(data.schedule('2026-09-28', '2026-09-30').empty)

    def test_rank_week_requires_eighty_factor_ready_candidates(self):
        data = rank_ready_dataset_fixture(candidate_count=79)

        week = rank_week(data, '2026-09-25', minimum_ready=80)

        self.assertIn('유효 후보 79/100', week['issues'])

    def test_signal_universe_uses_investability_decision_at_signal_timestamp(self):
        data = dataset_fixture()
        adapter = FakeInvestabilityAdapter(
            data.snapshots['2026-09-25'].Code, eligible_codes=['000001'],
        )
        data.investability_adapter = adapter

        week = data.week_inputs('2026-09-25')

        self.assertEqual(week['listing'].Code.tolist(), ['000001'])
        self.assertEqual(
            adapter.universe_calls,
            [('2026-09-25T15:30:00+09:00', 'investability-v1')],
        )
        self.assertEqual(
            week['investability_signal'].iloc[0].evidence_ids,
            ('evidence-000001',),
        )

    def test_252_session_rule_remains_strategy_readiness_not_investability(self):
        data = dataset_fixture()
        data.investability_adapter = FakeInvestabilityAdapter(
            data.snapshots['2026-09-25'].Code,
        )

        week = rank_week(data, '2026-09-25', minimum_ready=80)

        self.assertEqual(len(week['investability_signal']), 100)
        self.assertIn('유효 후보 0/100', week['issues'])


if __name__ == '__main__':
    unittest.main()
