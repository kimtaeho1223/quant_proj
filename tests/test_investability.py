import unittest

import pandas as pd

from tests.investability_fixtures import FakeLifecycleReader, sessions, status_rows


class InvestabilityModelTests(unittest.TestCase):
    def test_v1_policy_allows_only_kospi_kosdaq_common_shares(self):
        from quantdesk.investability import INVESTABILITY_V1, build_status_model, decision_at

        rows = status_rows(('2026-10-02',))
        preferred = rows.iloc[0].copy()
        preferred['standard_code'] = 'KR7005931001'
        preferred['short_code'] = '005935'
        preferred['security_kind'] = 'preferred'
        rows = pd.concat([rows, preferred.to_frame().T], ignore_index=True)
        model = build_status_model(
            rows, FakeLifecycleReader(), sessions('2026-10-02', '2026-10-05'),
        )

        common = decision_at(model, 'sec-samsung', '2026-10-02T15:30:00+09:00', INVESTABILITY_V1)
        excluded = decision_at(model, 'sec-preferred', '2026-10-02T15:30:00+09:00', INVESTABILITY_V1)

        self.assertEqual(common.state, 'eligible')
        self.assertEqual(excluded.state, 'ineligible')
        self.assertIn('security_kind_not_allowed', excluded.reason_codes)

    def test_each_blocking_flag_produces_ineligible_with_evidence(self):
        from quantdesk.investability import INVESTABILITY_V1, build_status_model, decision_at

        for flag in ('management_designation', 'trading_suspension', 'liquidation_trading'):
            with self.subTest(flag=flag):
                rows = status_rows(('2026-10-02',))
                rows.loc[0, flag] = True
                model = build_status_model(
                    rows, FakeLifecycleReader(), sessions('2026-10-02', '2026-10-05'),
                )
                decision = decision_at(
                    model, 'sec-samsung', '2026-10-02T15:30:00+09:00', INVESTABILITY_V1,
                )
                self.assertEqual(decision.state, 'ineligible')
                self.assertIn(flag, decision.reason_codes)
                self.assertTrue(decision.evidence_ids)

    def test_missing_day_between_snapshots_is_unknown_not_forward_filled(self):
        from quantdesk.investability import INVESTABILITY_V1, build_status_model, decision_at

        model = build_status_model(
            status_rows(('2026-10-01', '2026-10-05')),
            FakeLifecycleReader(),
            sessions('2026-10-01', '2026-10-02', '2026-10-05'),
        )

        decision = decision_at(
            model, 'sec-samsung', '2026-10-02T15:30:00+09:00', INVESTABILITY_V1,
        )

        self.assertEqual(decision.state, 'unknown')
        self.assertIn('missing_daily_coverage', decision.reason_codes)

    def test_release_known_after_signal_close_does_not_rewrite_signal_decision(self):
        from quantdesk.investability import INVESTABILITY_V1, build_status_model, decision_at

        previous = status_rows(('2026-10-01',), suspended=True)
        release = status_rows(('2026-10-02',), suspended=False)
        release.loc[0, 'published_at'] = '2026-10-02T18:00:00+09:00'
        model = build_status_model(
            pd.concat([previous, release], ignore_index=True),
            FakeLifecycleReader(), sessions('2026-10-01', '2026-10-02', '2026-10-05'),
        )

        at_close = decision_at(
            model, 'sec-samsung', '2026-10-02T15:30:00+09:00', INVESTABILITY_V1,
        )
        after_release = decision_at(
            model, 'sec-samsung', '2026-10-02T19:00:00+09:00', INVESTABILITY_V1,
        )

        self.assertEqual(at_close.state, 'ineligible')
        self.assertIn('trading_suspension', at_close.reason_codes)
        self.assertEqual(after_release.state, 'eligible')

    def test_date_only_evidence_becomes_known_on_next_session(self):
        from quantdesk.investability import INVESTABILITY_V1, build_status_model, decision_at

        rows = status_rows(('2026-10-02',))
        rows.loc[0, 'published_at'] = None
        model = build_status_model(
            rows, FakeLifecycleReader(), sessions('2026-10-02', '2026-10-05'),
        )

        same_day = decision_at(
            model, 'sec-samsung', '2026-10-02T23:00:00+09:00', INVESTABILITY_V1,
        )
        self.assertEqual(same_day.state, 'unknown')
        self.assertTrue(model.assertions.known_at.iloc[0].startswith('2026-10-05T00:00:00'))

    def test_reused_short_code_requires_unique_lifecycle_identity(self):
        from quantdesk.investability import build_status_model

        intervals = pd.DataFrame([
            dict(security_id='sec-old', standard_code='KR7000010001', short_code='000010',
                 name='과거', market='KOSPI', security_kind='common',
                 valid_from='2020-01-01', valid_to='2026-10-02'),
            dict(security_id='sec-new', standard_code='KR7000020000', short_code='000010',
                 name='신규', market='KOSPI', security_kind='common',
                 valid_from='2026-10-02', valid_to=None),
        ])
        rows = status_rows(('2026-10-02',))
        rows.loc[0, ['standard_code', 'short_code']] = [None, '000010']

        model = build_status_model(
            rows, FakeLifecycleReader(intervals), sessions('2026-10-02', '2026-10-05'),
        )

        self.assertFalse(model.assertions.empty)
        self.assertEqual(model.assertions.security_id.iloc[0], 'sec-new')

    def test_overlapping_contradictory_assertions_are_blocking(self):
        from quantdesk.investability import build_status_model

        rows = status_rows(('2026-10-02',))
        conflict = rows.iloc[0].copy()
        conflict['raw_version_id'] = 2
        conflict['trading_suspension'] = True
        rows = pd.concat([rows, conflict.to_frame().T], ignore_index=True)

        model = build_status_model(
            rows, FakeLifecycleReader(), sessions('2026-10-02', '2026-10-05'),
        )

        self.assertTrue(any(item.rule_id == 'contradictory_assertions' for item in model.findings))

    def test_not_listed_is_distinct_from_unknown(self):
        from quantdesk.investability import INVESTABILITY_V1, build_status_model, decision_at

        model = build_status_model(
            status_rows(('2026-10-02',)), FakeLifecycleReader(),
            sessions('2026-10-02', '2026-10-05'),
        )

        not_listed = decision_at(
            model, 'sec-missing', '2026-10-02T15:30:00+09:00', INVESTABILITY_V1,
        )

        self.assertEqual(not_listed.state, 'not_listed')

    def test_fingerprint_is_stable_under_input_row_reordering(self):
        from quantdesk.investability import (
            INVESTABILITY_V1,
            build_status_model,
            investability_fingerprint,
        )

        rows = status_rows(('2026-10-01', '2026-10-02'))
        calendar = sessions('2026-10-01', '2026-10-02', '2026-10-05')
        first = build_status_model(rows, FakeLifecycleReader(), calendar)
        second = build_status_model(rows.iloc[::-1].reset_index(drop=True), FakeLifecycleReader(), calendar)

        self.assertEqual(
            investability_fingerprint(first, INVESTABILITY_V1),
            investability_fingerprint(second, INVESTABILITY_V1),
        )


if __name__ == '__main__':
    unittest.main()
