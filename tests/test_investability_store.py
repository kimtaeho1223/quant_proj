import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import pandas as pd

from tests.investability_fixtures import FakeLifecycleReader, sessions, status_rows


class InvestabilityStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db_path = Path(self.folder.name) / 'market.db'

    def tearDown(self):
        self.folder.cleanup()

    def _model(self):
        from quantdesk.investability import build_status_model

        return build_status_model(
            status_rows(('2026-10-01', '2026-10-02')),
            FakeLifecycleReader(),
            sessions('2026-10-01', '2026-10-02', '2026-10-05'),
        )

    def test_initialize_is_idempotent_and_records_schema_version(self):
        from quantdesk.investability_store import InvestabilityStore

        store = InvestabilityStore(self.db_path)
        store.initialize()
        store.initialize()

        with store.connect() as db:
            version = db.execute('SELECT version FROM investability_schema').fetchone()[0]
            tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertEqual(version, 1)
        self.assertTrue({
            'investability_source_rows',
            'investability_source_selections',
            'investability_reconciliation_runs',
            'investability_readiness_summaries',
        }.issubset(tables))

    def test_raw_versions_are_append_only_and_checksum_unique(self):
        from quantdesk.investability_source import InvestabilityRawArtifact
        from quantdesk.investability_store import InvestabilityStore

        store = InvestabilityStore(self.db_path)
        artifact = InvestabilityRawArtifact(
            'daily_status', '2026-10-02', 'a' * 64,
            Path(self.folder.name) / 'a.gz', '2026-10-03T00:00:00+00:00',
            'https://data.krx.co.kr/status',
        )
        first = store.save_raw_version(artifact, status_rows(('2026-10-02',)))
        repeat = store.save_raw_version(artifact, status_rows(('2026-10-02',)))
        changed = store.save_raw_version(
            replace(artifact, sha256='b' * 64, path=Path(self.folder.name) / 'b.gz'),
            status_rows(('2026-10-02',)),
        )

        self.assertEqual(first, repeat)
        self.assertNotEqual(first, changed)
        self.assertEqual(len(store.raw_versions()), 2)
        with store.connect() as db:
            source_rows = db.execute(
                'SELECT COUNT(*) FROM investability_source_rows'
            ).fetchone()[0]
        self.assertEqual(source_rows, 2)

    def test_failed_promotion_rolls_back_all_model_tables(self):
        from quantdesk.investability import INVESTABILITY_V1
        from quantdesk.investability_store import InvestabilityStore

        store = InvestabilityStore(self.db_path)
        model = self._model()
        first = store.promote(model, INVESTABILITY_V1, {'2026-10-01': 1})
        broken = replace(model, assertions=model.assertions.drop(columns=['security_id']))

        with self.assertRaises(Exception):
            store.promote(broken, INVESTABILITY_V1, {'2026-10-02': 2})

        self.assertEqual(store.current_fingerprint(), first)
        self.assertEqual(len(store.universe('2026-10-02T15:30:00+09:00', 'investability-v1')), 1)

    def test_decision_and_universe_return_policy_and_evidence_metadata(self):
        from quantdesk.investability import INVESTABILITY_V1
        from quantdesk.investability_store import InvestabilityStore

        store = InvestabilityStore(self.db_path)
        store.promote(self._model(), INVESTABILITY_V1, {'2026-10-01': 1, '2026-10-02': 2})

        decision = store.decision(
            'sec-samsung', '2026-10-02T15:30:00+09:00', 'investability-v1',
        )
        universe = store.universe('2026-10-02T15:30:00+09:00', 'investability-v1')

        self.assertEqual(decision['state'], 'eligible')
        self.assertTrue(decision['evidence_ids'])
        self.assertTrue(decision['policy_fingerprint'])
        self.assertTrue(decision['model_fingerprint'])
        self.assertEqual(universe.security_id.tolist(), ['sec-samsung'])

    def test_execution_status_is_independent_from_signal_decision(self):
        from quantdesk.investability import INVESTABILITY_V1, build_status_model
        from quantdesk.investability_store import InvestabilityStore

        rows = status_rows(('2026-10-01', '2026-10-02'))
        rows.loc[rows.requested_date.eq('2026-10-02'), 'trading_suspension'] = True
        model = build_status_model(
            rows, FakeLifecycleReader(), sessions('2026-10-01', '2026-10-02'),
        )
        store = InvestabilityStore(self.db_path)
        store.promote(model, INVESTABILITY_V1, {'2026-10-01': 1, '2026-10-02': 2})

        signal = store.decision(
            'sec-samsung', '2026-10-01T15:30:00+09:00', 'investability-v1',
        )
        execution = store.execution_status(
            'sec-samsung', '2026-10-02', '2026-10-02T09:00:00+09:00',
            'investability-v1',
        )

        self.assertEqual(signal['state'], 'eligible')
        self.assertEqual(execution['state'], 'ineligible')
        self.assertIn('trading_suspension', execution['reason_codes'])

    def test_readiness_is_scoped_to_every_requested_date_and_policy(self):
        from quantdesk.investability import INVESTABILITY_V1
        from quantdesk.investability_store import InvestabilityStore

        store = InvestabilityStore(self.db_path)
        store.promote(self._model(), INVESTABILITY_V1, {'2026-10-01': 1, '2026-10-02': 2})

        ready = store.readiness('2026-10-01', '2026-10-02', 'investability-v1')
        missing = store.readiness('2026-10-01', '2026-10-05', 'investability-v1')
        unknown_policy = store.readiness('2026-10-01', '2026-10-02', 'missing-policy')

        self.assertTrue(ready['investability_ready'])
        self.assertFalse(missing['investability_ready'])
        self.assertIn('2026-10-05', missing['unknown_dates'])
        self.assertFalse(unknown_policy['investability_ready'])

    def test_readiness_blocks_range_without_session_calendar(self):
        from quantdesk.investability import INVESTABILITY_V1
        from quantdesk.investability_store import InvestabilityStore

        store = InvestabilityStore(self.db_path)
        store.promote(self._model(), INVESTABILITY_V1, {'2026-10-01': 1, '2026-10-02': 2})

        outside = store.readiness('2026-11-01', '2026-11-30', 'investability-v1')

        self.assertFalse(outside['investability_ready'])
        self.assertIn('missing_session_calendar', outside['blocking_codes'])

        overrun = store.readiness('2026-10-01', '2026-11-30', 'investability-v1')
        self.assertFalse(overrun['investability_ready'])
        self.assertIn('missing_session_calendar', overrun['blocking_codes'])

    def test_manual_review_cannot_mutate_raw_facts(self):
        from quantdesk.investability_source import InvestabilityRawArtifact
        from quantdesk.investability_store import InvestabilityStore

        store = InvestabilityStore(self.db_path)
        artifact = InvestabilityRawArtifact(
            'daily_status', '2026-10-02', 'a' * 64,
            Path(self.folder.name) / 'a.gz', '2026-10-03T00:00:00+00:00',
            'https://data.krx.co.kr/status',
        )
        raw_id = store.save_raw_version(artifact, status_rows(('2026-10-02',)))
        before = store.raw_versions().copy()
        review_id = store.record_manual_review(
            'coverage:2026-10-02', 'accept', '공식 정정 공지 확인',
            'local-user', [raw_id],
        )

        self.assertGreater(review_id, 0)
        pd.testing.assert_frame_equal(before, store.raw_versions())

    def test_rebuild_result_records_expected_and_actual_fingerprint(self):
        from quantdesk.investability_store import InvestabilityStore

        store = InvestabilityStore(self.db_path)
        rebuild_id = store.record_rebuild('expected', 'actual', 'failed', '불일치')

        latest = store.latest_rebuild()
        self.assertEqual(latest['id'], rebuild_id)
        self.assertEqual(latest['expected_fingerprint'], 'expected')
        self.assertEqual(latest['actual_fingerprint'], 'actual')
        self.assertEqual(latest['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
