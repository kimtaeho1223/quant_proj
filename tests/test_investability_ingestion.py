import tempfile
import unittest
from pathlib import Path

from tests.investability_fixtures import (
    FakeLifecycleReader,
    FakeResponse,
    STATUS_CSV,
    sessions,
    status_csv_bytes,
)


class InvestabilityIngestionTests(unittest.TestCase):
    def setUp(self):
        from quantdesk.investability_store import InvestabilityStore

        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.store = InvestabilityStore(self.root / 'market.db')
        self.archive = self.root / 'raw'
        self.lifecycle = FakeLifecycleReader()
        self.sessions = sessions('2026-10-02', '2026-10-05')

    def tearDown(self):
        self.folder.cleanup()

    def _ingest(self, payload=None):
        from quantdesk.investability_ingestion import ingest_investability_csv

        return ingest_investability_csv(
            self.store,
            self.archive,
            '2026-10-02',
            payload if payload is not None else status_csv_bytes(),
            self.lifecycle,
            self.sessions,
        )

    @staticmethod
    def _changed_payload():
        changed = STATUS_CSV.replace(
            '삼성전자,KOSPI,보통주,N,N,N',
            '삼성전자,KOSPI,보통주,Y,N,N',
        )
        return status_csv_bytes(changed)

    def test_collection_archives_before_parsing_and_preserves_valid_model_on_failure(self):
        from quantdesk.investability_ingestion import collect_investability_date

        first = self._ingest()
        fingerprint = self.store.current_fingerprint()

        result = collect_investability_date(
            self.store,
            self.archive,
            '2026-10-02',
            self.lifecycle,
            self.sessions,
            lambda *args, **kwargs: FakeResponse(b'bad,columns\nx,y\n'),
        )

        self.assertEqual(first['status'], 'promoted')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.store.current_fingerprint(), fingerprint)
        self.assertEqual(len(list(self.archive.rglob('*.csv.gz'))), 2)
        self.assertEqual(len(self.store.raw_versions()), 2)

    def test_official_csv_fallback_uses_same_validation_pipeline(self):
        result = self._ingest()
        decision = self.store.decision(
            'sec-samsung', '2026-10-02T15:30:00+09:00', 'investability-v1',
        )

        self.assertEqual(result['status'], 'promoted')
        self.assertEqual(result['source'], 'official_csv')
        self.assertEqual(decision['state'], 'eligible')

        invalid = self._ingest(b'bad,columns\nx,y\n')
        self.assertEqual(invalid['status'], 'failed')
        self.assertIn('필수 열', invalid['message'])

    def test_changed_same_coverage_payload_creates_unselected_revision_lock(self):
        first = self._ingest()
        fingerprint = self.store.current_fingerprint()
        revised = self._ingest(self._changed_payload())

        self.assertEqual(first['status'], 'promoted')
        self.assertEqual(revised['status'], 'revision_pending')
        self.assertNotEqual(first['raw_version_id'], revised['raw_version_id'])
        self.assertEqual(self.store.current_fingerprint(), fingerprint)
        self.assertEqual(
            self.store.source_selections()['2026-10-02']['raw_version_id'],
            first['raw_version_id'],
        )
        readiness = self.store.readiness(
            '2026-10-02', '2026-10-02', 'investability-v1',
        )
        self.assertFalse(readiness['investability_ready'])
        self.assertIn('unselected_revision', readiness['blocking_codes'])

    def test_selection_requires_nonempty_official_reason_for_revision(self):
        from quantdesk.investability_ingestion import select_investability_source

        self._ingest()
        revised = self._ingest(self._changed_payload())

        with self.assertRaisesRegex(ValueError, '사유'):
            select_investability_source(
                self.store, '2026-10-02', revised['raw_version_id'], '  ',
                self.lifecycle, self.sessions,
            )

        selected = select_investability_source(
            self.store,
            '2026-10-02',
            revised['raw_version_id'],
            'KRX 2026-10-02 정정 공지 반영',
            self.lifecycle,
            self.sessions,
        )
        decision = self.store.decision(
            'sec-samsung', '2026-10-02T15:30:00+09:00', 'investability-v1',
        )
        self.assertEqual(selected['status'], 'promoted')
        self.assertEqual(decision['state'], 'ineligible')
        self.assertIn('management_designation', decision['reason_codes'])

    def test_rebuild_from_selected_raw_versions_matches_promoted_fingerprint(self):
        from quantdesk.investability_ingestion import rebuild_investability_model

        promoted = self._ingest()
        rebuilt = rebuild_investability_model(
            self.store, self.lifecycle, self.sessions,
        )

        self.assertEqual(rebuilt['status'], 'verified')
        self.assertEqual(rebuilt['expected_fingerprint'], promoted['fingerprint'])
        self.assertEqual(rebuilt['actual_fingerprint'], promoted['fingerprint'])

    def test_rebuild_mismatch_keeps_readiness_locked(self):
        from quantdesk.investability_ingestion import rebuild_investability_model

        self._ingest()
        with self.store.connect() as db:
            db.execute(
                'UPDATE investability_model_meta SET fingerprint=? WHERE id=1',
                ('tampered-fingerprint',),
            )

        rebuilt = rebuild_investability_model(
            self.store, self.lifecycle, self.sessions,
        )
        readiness = self.store.readiness(
            '2026-10-02', '2026-10-02', 'investability-v1',
        )

        self.assertEqual(rebuilt['status'], 'mismatch')
        self.assertFalse(readiness['investability_ready'])
        self.assertIn('rebuild_mismatch', readiness['blocking_codes'])

    def test_authentication_failure_returns_actionable_fallback_status(self):
        from quantdesk.investability_ingestion import collect_investability_date

        result = collect_investability_date(
            self.store,
            self.archive,
            '2026-10-02',
            self.lifecycle,
            self.sessions,
            lambda *args, **kwargs: FakeResponse(b'LOGIN', 401),
            service_key='secret-key',
        )

        self.assertEqual(result['status'], 'action_required')
        self.assertIn('공식 CSV', result['message'])
        self.assertNotIn('secret-key', result['message'])
        self.assertIsNone(self.store.current_fingerprint())


if __name__ == '__main__':
    unittest.main()
