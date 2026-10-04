import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests.investability_fixtures import FakeLifecycleReader, sessions, status_rows


def market_rows(records):
    defaults = {
        'name': '삼성전자', 'market': 'KOSPI', 'close': 70000.0,
        'volume': 100.0, 'raw_version_id': 1,
    }
    return pd.DataFrame([{**defaults, **record} for record in records])


def lifecycle_with_second_common():
    intervals = FakeLifecycleReader().intervals.copy()
    intervals = pd.concat([intervals, pd.DataFrame([{
        'security_id': 'sec-sk', 'standard_code': 'KR7000660001',
        'short_code': '000660', 'name': 'SK하이닉스', 'market': 'KOSPI',
        'security_kind': 'common', 'valid_from': '1996-12-26', 'valid_to': None,
    }])], ignore_index=True)
    return FakeLifecycleReader(intervals)


class MissingMiddleLifecycle(FakeLifecycleReader):
    def listed_securities(self, as_of_date):
        day = pd.Timestamp(as_of_date).date().isoformat()
        if day == '2026-10-02':
            return self.intervals.iloc[0:0].copy()
        return super().listed_securities(as_of_date)


class InvestabilityReconciliationTests(unittest.TestCase):
    def _model(self, dates=('2026-10-01', '2026-10-02'), reader=None, calendar=None):
        from quantdesk.investability import build_status_model

        reader = reader if reader is not None else FakeLifecycleReader()
        calendar = calendar if calendar is not None else sessions(*dates)
        return build_status_model(status_rows(dates), reader, calendar)

    def test_status_outside_listing_interval_is_blocking(self):
        from quantdesk.investability import INVESTABILITY_V1, reconcile_investability

        model = self._model(('2026-10-02',))
        expired = FakeLifecycleReader()
        expired.intervals.loc[:, 'valid_to'] = '2026-10-02'

        _, findings = reconcile_investability(
            model, expired,
            market_rows([{'trade_date': '2026-10-02', 'code': '005930'}]),
            '2026-10-02', '2026-10-02', INVESTABILITY_V1,
        )

        finding = next(item for item in findings if item.rule_id == 'status_outside_listing')
        self.assertEqual(finding.severity, 'blocking')
        self.assertEqual(finding.requested_date, '2026-10-02')

    def test_listed_security_without_selected_daily_status_is_unknown(self):
        from quantdesk.investability import INVESTABILITY_V1, reconcile_investability

        reader = lifecycle_with_second_common()
        model = self._model(('2026-10-02',), reader)
        results, findings = reconcile_investability(
            model, reader,
            market_rows([
                {'trade_date': '2026-10-02', 'code': '005930'},
                {'trade_date': '2026-10-02', 'code': '000660', 'name': 'SK하이닉스'},
            ]),
            '2026-10-02', '2026-10-02', INVESTABILITY_V1,
        )

        sk = results[results.security_id.eq('sec-sk')].iloc[0]
        self.assertEqual(sk.state, 'unknown')
        self.assertIn('missing_selected_daily_status', sk.reason_codes)
        self.assertTrue(any(
            item.rule_id == 'missing_selected_daily_status'
            and item.security_id == 'sec-sk' for item in findings
        ))

    def test_market_row_without_lifecycle_identity_is_quarantined(self):
        from quantdesk.investability import INVESTABILITY_V1, reconcile_investability

        model = self._model(('2026-10-02',))
        _, findings = reconcile_investability(
            model, FakeLifecycleReader(),
            market_rows([{
                'trade_date': '2026-10-02', 'code': '999999', 'name': '미등록종목',
            }]),
            '2026-10-02', '2026-10-02', INVESTABILITY_V1,
        )

        finding = next(item for item in findings if item.rule_id == 'market_without_lifecycle')
        self.assertEqual(finding.severity, 'quarantine')

    def test_zero_volume_does_not_create_suspension(self):
        from quantdesk.investability import INVESTABILITY_V1, reconcile_investability

        model = self._model(('2026-10-02',))
        results, findings = reconcile_investability(
            model, FakeLifecycleReader(),
            market_rows([{
                'trade_date': '2026-10-02', 'code': '005930', 'volume': 0,
            }]),
            '2026-10-02', '2026-10-02', INVESTABILITY_V1,
        )

        samsung = results[results.security_id.eq('sec-samsung')].iloc[0]
        self.assertEqual(samsung.state, 'eligible')
        self.assertNotIn('trading_suspension', samsung.reason_codes)
        self.assertFalse(any(item.rule_id == 'inferred_suspension' for item in findings))

    def test_price_gap_is_warning_only_without_official_status(self):
        from quantdesk.investability import INVESTABILITY_V1, reconcile_investability

        reader = lifecycle_with_second_common()
        model = self._model(('2026-10-01', '2026-10-02'), reader)
        _, findings = reconcile_investability(
            model, reader,
            market_rows([
                {'trade_date': '2026-10-01', 'code': '000660', 'name': 'SK하이닉스', 'close': 100},
                {'trade_date': '2026-10-02', 'code': '000660', 'name': 'SK하이닉스', 'close': 300},
            ]),
            '2026-10-01', '2026-10-02', INVESTABILITY_V1,
        )

        gap = next(item for item in findings if item.rule_id == 'price_gap')
        self.assertEqual(gap.severity, 'warning')
        self.assertFalse(any(
            item.rule_id == 'trading_suspension' and item.security_id == 'sec-sk'
            for item in findings
        ))

    def test_absent_middle_snapshot_remains_unknown_even_when_neighbors_match(self):
        from quantdesk.investability import INVESTABILITY_V1, build_status_model, reconcile_investability

        calendar = sessions('2026-10-01', '2026-10-02', '2026-10-05')
        model = build_status_model(
            status_rows(('2026-10-01', '2026-10-05')),
            FakeLifecycleReader(), calendar,
        )
        results, _ = reconcile_investability(
            model, FakeLifecycleReader(), pd.DataFrame(),
            '2026-10-01', '2026-10-05', INVESTABILITY_V1,
        )

        middle = results[
            results.requested_date.eq('2026-10-02')
            & results.security_id.eq('sec-samsung')
        ].iloc[0]
        self.assertEqual(middle.state, 'unknown')
        self.assertIn('missing_selected_daily_status', middle.reason_codes)

    def test_reconciliation_is_date_scoped_and_does_not_hide_failed_days(self):
        from quantdesk.investability import INVESTABILITY_V1, reconcile_investability
        from quantdesk.investability_store import InvestabilityStore

        calendar = sessions('2026-10-01', '2026-10-02', '2026-10-05')
        model = self._model(
            ('2026-10-01', '2026-10-02', '2026-10-05'),
            FakeLifecycleReader(), calendar,
        )
        results, findings = reconcile_investability(
            model, MissingMiddleLifecycle(),
            market_rows([
                {'trade_date': day, 'code': '005930', 'raw_version_id': index}
                for index, day in enumerate(model.sessions, start=1)
            ]),
            '2026-10-01', '2026-10-05', INVESTABILITY_V1,
        )

        with tempfile.TemporaryDirectory() as folder:
            store = InvestabilityStore(Path(folder) / 'market.db')
            store.promote(model, INVESTABILITY_V1, {})
            run_id = store.record_reconciliation(
                '2026-10-01', '2026-10-05', results, findings,
                source_model_fingerprint=store.current_fingerprint(),
                lifecycle_fingerprint='lifecycle-v1',
                market_raw_version='1,2,3',
                policy_fingerprint=INVESTABILITY_V1.fingerprint,
            )
            with store.connect() as db:
                audit = db.execute('''SELECT lifecycle_fingerprint,market_raw_version,
                    policy_fingerprint FROM investability_reconciliation_runs
                    WHERE id=?''', (run_id,)).fetchone()
                saved_findings = db.execute('''SELECT COUNT(*)
                    FROM investability_reconciliation_findings WHERE run_id=?''',
                    (run_id,)).fetchone()[0]

            first_day = store.readiness(
                '2026-10-01', '2026-10-01', 'investability-v1',
            )
            full_range = store.readiness(
                '2026-10-01', '2026-10-05', 'investability-v1',
            )

        self.assertTrue(first_day['investability_ready'])
        self.assertFalse(full_range['investability_ready'])
        self.assertEqual(full_range['reconciliation_blocked_dates'], ['2026-10-02'])
        self.assertEqual(audit, ('lifecycle-v1', '1,2,3', INVESTABILITY_V1.fingerprint))
        self.assertEqual(saved_findings, len(findings))


if __name__ == '__main__':
    unittest.main()
