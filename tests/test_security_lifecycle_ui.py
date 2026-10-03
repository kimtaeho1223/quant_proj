import os
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from quantdesk.security_lifecycle_ingestion import SecurityLifecycleIngestion
from quantdesk.security_lifecycle_source import LifecycleRawArchive
from quantdesk.security_lifecycle_store import SecurityLifecycleStore
from quantdesk.security_lifecycle_ui import import_lifecycle_csv
from tests.test_security_lifecycle_ingestion import official_sources


class SecurityLifecycleUiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        os.environ['QUANTDESK_MARKET_DB'] = str(root / 'market.db')
        os.environ['QUANTDESK_RAW_ROOT'] = str(root / 'daily')
        os.environ['QUANTDESK_LIFECYCLE_RAW_ROOT'] = str(root / 'lifecycle')
        fixture = Path(__file__).with_name('historical_market_ui_fixture_app.py')
        self.app = AppTest.from_file(str(fixture)).run(timeout=30)

    def tearDown(self):
        for name in [
            'QUANTDESK_MARKET_DB', 'QUANTDESK_RAW_ROOT',
            'QUANTDESK_LIFECYCLE_RAW_ROOT',
        ]:
            os.environ.pop(name, None)
        self.folder.cleanup()

    def ingestion(self):
        return SecurityLifecycleIngestion(
            SecurityLifecycleStore(os.environ['QUANTDESK_MARKET_DB']),
            LifecycleRawArchive(os.environ['QUANTDESK_LIFECYCLE_RAW_ROOT']),
        )

    def test_lifecycle_section_shows_four_source_states_and_separate_readiness_locks(self):
        labels = {metric.label for metric in self.app.metric}

        self.assertEqual(len(self.app.exception), 0)
        self.assertTrue({'종목 마스터', '신규 상장', '상장 폐지', '식별자 변경'} <= labels)
        self.assertTrue({
            '생애주기 준비', '투자 가능성 준비', '기업행사 준비', '공식 백테스트 준비',
        } <= labels)
        values = {metric.label: metric.value for metric in self.app.metric}
        self.assertEqual(values['투자 가능성 준비'], '잠김')
        self.assertEqual(values['기업행사 준비'], '잠김')
        self.assertEqual(values['공식 백테스트 준비'], '잠김')

    def test_csv_import_uses_identical_ingestion_path_and_never_exposes_raw_editor(self):
        source = official_sources()['security_master']

        imported = import_lifecycle_csv(
            os.environ['QUANTDESK_MARKET_DB'],
            os.environ['QUANTDESK_LIFECYCLE_RAW_ROOT'],
            'security_master', source.content, source.scope_start, source.scope_end,
        )

        store = SecurityLifecycleStore(os.environ['QUANTDESK_MARKET_DB'])
        rows = store.source_rows(imported['raw_version_id'])
        self.assertEqual(rows.iloc[0].short_code, '005930')
        self.app.run()
        self.assertEqual(len(self.app.get('data_editor')), 0)

    def test_changed_source_requires_version_selection_and_nonempty_evidence(self):
        first = official_sources()['security_master']
        changed = official_sources('삼성전자 변경')['security_master']
        for source in [first, changed]:
            import_lifecycle_csv(
                os.environ['QUANTDESK_MARKET_DB'],
                os.environ['QUANTDESK_LIFECYCLE_RAW_ROOT'],
                source.dataset, source.content, source.scope_start, source.scope_end,
            )
        self.app.run()

        button = self.app.button(key='lifecycle_select_security_master')
        self.assertTrue(button.disabled)
        self.app.text_input(key='lifecycle_evidence_security_master').set_value(
            'KRX 공식 정정 공지 2026-10-03'
        ).run()
        self.app.button(key='lifecycle_select_security_master').click().run()

        store = SecurityLifecycleStore(os.environ['QUANTDESK_MARKET_DB'])
        latest = int(store.raw_versions('security_master').id.max())
        self.assertEqual(store.source_selection()['security_master'], latest)

    def test_security_timeline_shows_identifiers_events_intervals_and_checksums(self):
        ingestion = self.ingestion()
        for source in official_sources().values():
            ingestion.ingest(source)
        ingestion.build_and_promote()

        self.app.run()

        self.assertIsNotNone(self.app.selectbox(key='lifecycle_security'))
        frames = [item.value for item in self.app.dataframe]
        columns = {column for frame in frames for column in frame.columns}
        self.assertTrue({'event_type', 'valid_from', 'sha256'} <= columns)

    def test_rebuild_mismatch_remains_blocked_and_visible(self):
        ingestion = self.ingestion()
        for source in official_sources().values():
            ingestion.ingest(source)
        ingestion.build_and_promote()
        changed = ingestion.ingest(official_sources('삼성전자 변경')['security_master'])
        ingestion.select_raw_version(
            'security_master', changed['raw_version_id'], 'KRX 공식 정정 공지 2026-10-03'
        )
        ingestion.rebuild()

        self.app.run()

        messages = [item.value for item in self.app.error]
        self.assertTrue(any('불일치' in message for message in messages))

    def test_existing_daily_collection_controls_still_work(self):
        self.assertEqual(self.app.button(key='historical_start').label, '전체시장 수집 시작')
        self.assertTrue(self.app.button(key='historical_start').disabled)
        self.assertEqual(len(self.app.exception), 0)


if __name__ == '__main__':
    unittest.main()
