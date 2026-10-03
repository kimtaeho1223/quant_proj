import unittest

import pandas as pd

from tests.security_lifecycle_fixtures import MASTER_CSV, normalized_lifecycle_rows
from quantdesk.security_lifecycle import (
    build_lifecycle_model,
    lifecycle_fingerprint,
    normalize_lifecycle_rows,
)


class SecurityLifecycleTests(unittest.TestCase):
    def test_normalization_requires_official_identity_and_effective_dates(self):
        frame = pd.read_csv(pd.io.common.StringIO(MASTER_CSV), dtype=str)
        normalized = normalize_lifecycle_rows(
            frame, 'security_master', 4, '2026-10-03T00:00:00+00:00',
        )
        self.assertEqual(normalized.short_code.tolist(), ['005930', '005935'])
        self.assertEqual(set(normalized.event_type), {'listing'})
        self.assertEqual(set(normalized.raw_version_id), {4})
        with self.assertRaisesRegex(ValueError, '표준코드'):
            normalize_lifecycle_rows(
                frame.drop(columns='표준코드'), 'security_master', 4,
                '2026-10-03T00:00:00+00:00',
            )

    def test_future_listing_is_absent_and_delisted_security_remains_before_delisting(self):
        model = build_lifecycle_model(normalized_lifecycle_rows())
        past = model.listed_on('2024-06-28')
        after = model.listed_on('2024-07-01')
        self.assertIn('000120', past.short_code.tolist())
        self.assertNotIn('000120', after.short_code.tolist())

    def test_listing_is_inclusive_and_delisting_is_exclusive(self):
        model = build_lifecycle_model(normalized_lifecycle_rows())
        self.assertIn('000120', model.listed_on('2020-01-02').short_code.tolist())
        self.assertNotIn('000120', model.listed_on('2020-01-01').short_code.tolist())
        self.assertNotIn('000120', model.listed_on('2024-07-01').short_code.tolist())

    def test_name_code_and_market_changes_create_non_overlapping_intervals(self):
        rows = normalized_lifecycle_rows()
        change = rows.iloc[[0]].copy()
        change.loc[:, ['dataset', 'raw_version_id', 'source_row_number']] = ['identifier_changes', 3, 1]
        change.loc[:, ['previous_code', 'previous_name', 'previous_market']] = ['123456', '옛이름', 'KOSDAQ']
        change.loc[:, ['short_code', 'name', 'market']] = ['005930', '삼성전자', 'KOSPI']
        change.loc[:, ['event_type', 'effective_date', 'listing_date']] = ['identifier_change', '2022-01-03', '2020-01-02']
        model = build_lifecycle_model(pd.concat([rows.iloc[1:], change], ignore_index=True))
        intervals = model.intervals[model.intervals.short_code.isin(['123456', '005930'])]
        self.assertEqual(intervals[['valid_from', 'valid_to']].values.tolist(), [
            ['2020-01-02', '2022-01-03'], ['2022-01-03', None],
        ])

    def test_common_and_preferred_shares_remain_distinct_under_one_official_issuer(self):
        model = build_lifecycle_model(normalized_lifecycle_rows())
        samsung = model.securities[model.securities.issuer_id.notna()]
        self.assertEqual(len(samsung.issuer_id.unique()), 2)  # Samsung group plus C002 issuer
        same_issuer = samsung[samsung.standard_code.str.startswith('KR700593')]
        self.assertEqual(len(same_issuer.security_id.unique()), 2)
        self.assertEqual(len(same_issuer.issuer_id.unique()), 1)

    def test_last_trading_date_is_not_inferred_from_delisting_date(self):
        rows = normalized_lifecycle_rows()
        rows.loc[2, 'last_trading_date'] = None
        model = build_lifecycle_model(rows)
        event = model.events[model.events.event_type.eq('delisting')].iloc[0]
        self.assertIsNone(event.last_trading_date)
        self.assertEqual(event.effective_date, '2024-07-01')

    def test_code_reuse_is_allowed_only_in_non_overlapping_intervals(self):
        rows = normalized_lifecycle_rows()
        reused = rows.iloc[[2]].copy()
        reused.loc[:, ['standard_code', 'listing_date', 'effective_date', 'delisting_date']] = [
            'KR7999990001', '2024-07-01', '2024-07-01', None,
        ]
        reused.loc[:, 'event_type'] = 'listing'
        clean = build_lifecycle_model(pd.concat([rows, reused], ignore_index=True))
        self.assertFalse(any(f.rule_id == 'overlapping_code' for f in clean.findings))
        reused.loc[:, ['listing_date', 'effective_date']] = ['2024-06-30', '2024-06-30']
        broken = build_lifecycle_model(pd.concat([rows, reused], ignore_index=True))
        self.assertTrue(any(f.rule_id == 'overlapping_code' for f in broken.findings))

    def test_unknown_classification_and_unsupported_lineage_are_blocking(self):
        rows = normalized_lifecycle_rows()
        rows.loc[0, 'stock_type'] = '알수없음'
        rows.loc[1, ['event_type', 'successor_standard_code']] = ['merger', 'KR7005930003']
        model = build_lifecycle_model(rows)
        blocking = {f.rule_id for f in model.findings if f.severity == 'blocking'}
        self.assertEqual(blocking, {'unknown_classification', 'unsupported_lineage'})

    def test_fingerprint_is_order_independent_and_changes_with_canonical_content(self):
        rows = normalized_lifecycle_rows()
        first = lifecycle_fingerprint(build_lifecycle_model(rows))
        second = lifecycle_fingerprint(build_lifecycle_model(rows.sample(frac=1, random_state=7)))
        self.assertEqual(first, second)
        changed = rows.copy()
        changed.loc[0, 'name'] = '변경된이름'
        self.assertNotEqual(first, lifecycle_fingerprint(build_lifecycle_model(changed)))


if __name__ == '__main__':
    unittest.main()
