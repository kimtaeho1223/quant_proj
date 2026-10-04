import os
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from quantdesk.investability_ingestion import ingest_investability_csv
from quantdesk.investability_store import InvestabilityStore
from tests.investability_fixtures import (
    FakeLifecycleReader,
    STATUS_CSV,
    sessions,
    status_csv_bytes,
)


class InvestabilityUiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db_path = Path(self.folder.name) / 'market.db'
        self.raw_root = Path(self.folder.name) / 'raw'
        os.environ['QUANTDESK_MARKET_DB'] = str(self.db_path)
        os.environ['QUANTDESK_INVESTABILITY_RAW_ROOT'] = str(self.raw_root)
        self.store = InvestabilityStore(self.db_path)
        self.lifecycle = FakeLifecycleReader()
        self.calendar = sessions('2026-10-02', '2026-10-05')

    def tearDown(self):
        os.environ.pop('QUANTDESK_MARKET_DB', None)
        os.environ.pop('QUANTDESK_INVESTABILITY_RAW_ROOT', None)
        self.folder.cleanup()

    def _app(self):
        fixture = Path(__file__).with_name('investability_ui_fixture_app.py')
        return AppTest.from_file(str(fixture), default_timeout=40).run()

    def _ingest(self, payload=None):
        return ingest_investability_csv(
            self.store, self.raw_root, '2026-10-02',
            payload if payload is not None else status_csv_bytes(),
            self.lifecycle, self.calendar,
        )

    def test_ui_shows_collection_and_official_csv_fallback_without_cookie_input(self):
        app = self._app()

        self.assertEqual(len(app.exception), 0)
        self.assertIsNotNone(app.button(key='investability_collect'))
        self.assertTrue(app.button(key='investability_csv_import').disabled)
        self.assertFalse(any('쿠키' in item.label for item in app.text_input))

    def test_ui_requires_reason_before_selecting_changed_revision(self):
        self._ingest()
        changed = status_csv_bytes(STATUS_CSV.replace(
            '삼성전자,KOSPI,보통주,N,N,N',
            '삼성전자,KOSPI,보통주,Y,N,N',
        ))
        self._ingest(changed)

        app = self._app()

        self.assertTrue(app.button(key='investability_select_revision').disabled)
        app.text_input(key='investability_revision_reason').set_value(
            'KRX 정정 공지 확인',
        ).run()
        self.assertFalse(app.button(key='investability_select_revision').disabled)

    def test_ui_ignores_superseded_raw_revision_after_latest_selected(self):
        self._ingest()
        changed = status_csv_bytes(STATUS_CSV.replace(
            '삼성전자,KOSPI,보통주,N,N,N',
            '삼성전자,KOSPI,보통주,Y,N,N',
        ))
        result = self._ingest(changed)
        from quantdesk.investability_ingestion import select_investability_source
        select_investability_source(
            self.store, '2026-10-02', result['raw_version_id'],
            'KRX 정정 공지 확인', self.lifecycle, self.calendar,
        )

        app = self._app()

        self.assertFalse(any(
            '선택 사유가 필요한 공식 수정본' in item.value
            for item in app.warning
        ))

    def test_ui_shows_unknown_dates_and_evidence_backed_exclusions(self):
        self._ingest()

        app = self._app()

        rendered = ' '.join(
            item.value for group in (app.warning, app.info, app.caption)
            for item in group
        )
        self.assertIn('2026-10-05', rendered)
        tables = ' '.join(item.value.to_csv(index=False) for item in app.dataframe)
        self.assertIn('security_kind_not_allowed', tables)
        self.assertIn('evidence', tables)

    def test_ui_distinguishes_all_four_readiness_locks(self):
        self._ingest()

        app = self._app()
        metrics = {item.label: item.value for item in app.metric}

        self.assertEqual(set(metrics) & {
            '종목 생애주기', '투자 가능성', '기업행사', '공식 백테스트',
        }, {'종목 생애주기', '투자 가능성', '기업행사', '공식 백테스트'})
        self.assertEqual(metrics['기업행사'], '잠금')
        self.assertEqual(metrics['공식 백테스트'], '잠금')

    def test_ui_rebuild_mismatch_is_blocking(self):
        self._ingest()
        self.store.record_rebuild('expected', 'actual', 'failed', '불일치')

        app = self._app()

        self.assertTrue(any('재빌드' in item.value for item in app.error))
        metrics = {item.label: item.value for item in app.metric}
        self.assertEqual(metrics['투자 가능성'], '잠금')

    def test_uploaded_filename_cannot_control_archive_path(self):
        from quantdesk.investability_ui import uploaded_csv_bytes

        class Uploaded:
            name = '..\\..\\outside.csv'

            @staticmethod
            def getvalue():
                return status_csv_bytes()

        result = ingest_investability_csv(
            self.store, self.raw_root, '2026-10-02',
            uploaded_csv_bytes(Uploaded()), self.lifecycle, self.calendar,
        )
        raw = self.store.raw_version(result['raw_version_id'])

        self.assertTrue(Path(raw['path']).resolve().is_relative_to(self.raw_root.resolve()))
        self.assertNotIn('outside.csv', raw['path'])

    def test_ui_does_not_claim_official_backtest_readiness(self):
        self._ingest()

        app = self._app()
        metrics = {item.label: item.value for item in app.metric}
        success_text = ' '.join(item.value for item in app.success)

        self.assertEqual(metrics['공식 백테스트'], '잠금')
        self.assertNotIn('공식 백테스트 준비 완료', success_text)


if __name__ == '__main__':
    unittest.main()
