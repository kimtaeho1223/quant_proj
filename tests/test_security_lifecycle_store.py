import tempfile
import unittest
from pathlib import Path

from quantdesk.security_lifecycle import build_lifecycle_model, lifecycle_fingerprint
from quantdesk.security_lifecycle_source import LifecycleArchivedRaw
from quantdesk.security_lifecycle_store import SecurityLifecycleStore
from tests.security_lifecycle_fixtures import normalized_lifecycle_rows


class SecurityLifecycleStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'market.db'
        self.store = SecurityLifecycleStore(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def raw(self, dataset='security_master', digest='a' * 64):
        return LifecycleArchivedRaw(
            dataset, '2022-01-01', '2026-12-31', digest,
            Path(self.folder.name) / f'{digest}.gz', '2026-10-03T00:00:00+00:00',
        )

    def selected_sources(self):
        selected = {}
        for index, dataset in enumerate(
            ['security_master', 'new_listings', 'delistings', 'identifier_changes'], 1,
        ):
            raw_id = self.store.record_raw_version(self.raw(dataset, str(index) * 64), {}, '1')
            self.store.select_raw_version(dataset, raw_id, '최초 공식 원본')
            selected[dataset] = raw_id
        return selected

    def test_raw_manifest_and_normalized_rows_round_trip_with_evidence(self):
        raw_id = self.store.record_raw_version(self.raw(), {'screen': 'MDCSTAT019'}, '1')
        rows = normalized_lifecycle_rows().iloc[[0]].copy()
        self.store.save_source_rows(raw_id, rows)
        loaded = self.store.source_rows(raw_id)
        self.assertEqual(loaded.standard_code.tolist(), ['KR7005930003'])
        self.assertEqual(loaded.raw_version_id.tolist(), [raw_id])
        self.assertEqual(self.store.raw_versions('security_master').iloc[0].sha256, 'a' * 64)

    def test_model_promotion_replaces_all_derived_tables_atomically(self):
        selected = self.selected_sources()
        model = build_lifecycle_model(normalized_lifecycle_rows())
        revision = self.store.promote_model(model, selected)
        self.assertEqual(revision, 1)
        self.assertEqual(self.store.current_fingerprint(), lifecycle_fingerprint(model))
        self.assertEqual(len(self.store.listed_securities('2024-06-28')), 3)

    def test_failed_promotion_preserves_previous_model_and_fingerprint(self):
        selected = self.selected_sources()
        model = build_lifecycle_model(normalized_lifecycle_rows())
        self.store.promote_model(model, selected)
        before = self.store.current_fingerprint()
        changed_rows = normalized_lifecycle_rows().copy()
        changed_rows.loc[0, 'name'] = '바뀐이름'

        broken = SecurityLifecycleStore(
            self.path, promotion_hook=lambda stage: (_ for _ in ()).throw(RuntimeError('stop'))
            if stage == 'after_identities' else None,
        )
        with self.assertRaisesRegex(RuntimeError, 'stop'):
            broken.promote_model(build_lifecycle_model(changed_rows), selected)

        self.assertEqual(self.store.current_fingerprint(), before)
        self.assertEqual(self.store.listed_securities('2024-06-28').iloc[0]['name'], '과거종목')

    def test_listed_securities_respects_inclusive_and_exclusive_dates(self):
        self.store.promote_model(build_lifecycle_model(normalized_lifecycle_rows()), self.selected_sources())
        self.assertIn('000120', self.store.listed_securities('2020-01-02').short_code.tolist())
        self.assertNotIn('000120', self.store.listed_securities('2024-07-01').short_code.tolist())

    def test_timeline_returns_events_intervals_and_source_evidence(self):
        model = build_lifecycle_model(normalized_lifecycle_rows())
        self.store.promote_model(model, self.selected_sources())
        security_id = model.securities[model.securities.standard_code.eq('KR7000120006')].iloc[0].security_id
        timeline = self.store.lifecycle_timeline(security_id)
        self.assertEqual({item['event_type'] for item in timeline['events']}, {'listing', 'delisting'})
        self.assertEqual(timeline['intervals'][0]['raw_version_id'], 2)

    def test_readiness_blocks_unselected_revisions_and_blocking_findings(self):
        selected = self.selected_sources()
        self.store.promote_model(build_lifecycle_model(normalized_lifecycle_rows()), selected)
        ready = self.store.readiness('2022-01-01', '2024-06-30')
        self.assertTrue(ready['lifecycle_ready'])
        self.store.record_raw_version(self.raw('security_master', 'f' * 64), {}, '1')
        blocked = self.store.readiness('2022-01-01', '2024-06-30')
        self.assertFalse(blocked['lifecycle_ready'])
        self.assertIn('검토되지 않은 원본 버전', ' '.join(blocked['blocking_reasons']))

    def test_rebuild_record_preserves_parser_rules_sources_and_fingerprint(self):
        record_id = self.store.record_rebuild(
            parser_version='2', rules_version='3', source_selection={'security_master': 7},
            expected_fingerprint='abc', actual_fingerprint='abc', status='passed', message='일치',
        )
        record = self.store.rebuilds().set_index('id').loc[record_id]
        self.assertEqual(record.parser_version, '2')
        self.assertEqual(record.rules_version, '3')
        self.assertIn('security_master', record.source_selection_json)


if __name__ == '__main__':
    unittest.main()
