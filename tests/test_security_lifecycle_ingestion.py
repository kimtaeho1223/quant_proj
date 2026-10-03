import tempfile
import unittest
from pathlib import Path

from quantdesk.security_lifecycle_ingestion import SecurityLifecycleIngestion
from quantdesk.security_lifecycle_source import LifecycleRawArchive, LifecycleSourceResult
from quantdesk.security_lifecycle_store import SecurityLifecycleStore


HEADER = (
    '표준코드,단축코드,한글 종목명,시장구분,증권구분,주식종류,상장일,'
    '최종매매일,상장폐지일,회사코드,변경구분,변경일,이전종목코드,'
    '이전종목명,이전시장,승계표준코드\n'
)


def source_result(dataset, row, scope_start='2020-01-01', scope_end='2026-12-31'):
    return LifecycleSourceResult(
        dataset=dataset,
        scope_start=scope_start,
        scope_end=scope_end,
        content=(HEADER + row + '\n').encode('utf-8-sig'),
        metadata={'provider': 'KRX', 'source_url': 'https://data.krx.co.kr/official'},
    )


def official_sources(master_name='삼성전자'):
    return {
        'security_master': source_result(
            'security_master',
            f'KR7005930003,005930,{master_name},KOSPI,주권,보통주,1975-06-11,,,C001,,,,,,',
        ),
        'new_listings': source_result(
            'new_listings',
            'KR7000660001,000660,SK하이닉스,KOSPI,주권,보통주,1996-12-26,,,C002,,,,,,',
        ),
        'delistings': source_result(
            'delistings',
            'KR7000120006,000120,과거종목,KOSPI,주권,보통주,2020-01-02,2024-06-27,2024-07-01,C003,,,,,,',
        ),
        'identifier_changes': source_result(
            'identifier_changes',
            'KR7035420009,035420,NAVER,KOSPI,주권,보통주,2002-10-29,,,C004,identifier_change,2021-01-01,035420,네이버,KOSPI,',
        ),
    }


class SecurityLifecycleIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = SecurityLifecycleStore(root / 'market.sqlite3')
        self.ingestion = SecurityLifecycleIngestion(
            self.store, LifecycleRawArchive(root / 'raw')
        )

    def tearDown(self):
        self.temporary.cleanup()

    def ingest_all(self, sources=None):
        results = {}
        for dataset, source in (sources or official_sources()).items():
            results[dataset] = self.ingestion.ingest(source)
        return results

    def test_ingest_archives_normalizes_and_registers_without_premature_promotion(self):
        result = self.ingestion.ingest(official_sources()['security_master'])

        self.assertEqual(result['dataset'], 'security_master')
        self.assertTrue(Path(result['archive_path']).exists())
        self.assertEqual(len(self.store.raw_versions('security_master')), 1)
        rows = self.store.source_rows(result['raw_version_id'])
        self.assertEqual(rows.iloc[0].short_code, '005930')
        self.assertIsNone(self.store.current_fingerprint())

    def test_duplicate_ingest_is_idempotent(self):
        source = official_sources()['security_master']
        first = self.ingestion.ingest(source)
        second = self.ingestion.ingest(source)

        self.assertEqual(first['raw_version_id'], second['raw_version_id'])
        self.assertEqual(len(self.store.raw_versions('security_master')), 1)
        self.assertEqual(len(self.store.source_rows(first['raw_version_id'])), 1)

    def test_first_valid_version_is_selected_without_manual_override(self):
        result = self.ingestion.ingest(official_sources()['security_master'])

        self.assertEqual(
            self.store.source_selection()['security_master'], result['raw_version_id']
        )
        self.assertTrue(result['selected'])

    def test_changed_checksum_for_same_scope_requires_evidence_backed_selection(self):
        first = self.ingestion.ingest(official_sources()['security_master'])
        changed = self.ingestion.ingest(official_sources('삼성전자 변경')['security_master'])

        self.assertNotEqual(first['raw_version_id'], changed['raw_version_id'])
        self.assertEqual(
            self.store.source_selection()['security_master'], first['raw_version_id']
        )
        self.assertFalse(changed['selected'])
        self.assertIn(
            '검토되지 않은 원본 버전',
            ' '.join(self.store.readiness('2020-01-01', '2026-12-31')['blocking_reasons']),
        )
        with self.assertRaisesRegex(ValueError, '근거'):
            self.ingestion.select_raw_version('security_master', changed['raw_version_id'], ' ')
        with self.assertRaisesRegex(ValueError, '일치하지'):
            self.ingestion.select_raw_version('delistings', changed['raw_version_id'], '공식 정정 공지')

        self.ingestion.select_raw_version(
            'security_master', changed['raw_version_id'], 'KRX 공식 정정 공지 2026-10-03'
        )
        self.assertEqual(
            self.store.source_selection()['security_master'], changed['raw_version_id']
        )

    def test_build_refuses_missing_required_dataset_or_blocking_finding(self):
        self.ingestion.ingest(official_sources()['security_master'])
        with self.assertRaisesRegex(ValueError, '선택되지 않은 공식 자료'):
            self.ingestion.build_and_promote()

        sources = official_sources()
        sources['new_listings'] = source_result(
            'new_listings',
            'KR7000660001,000660,SK하이닉스,KOSPI,주권,알수없음,1996-12-26,,,C002,,,,,,',
        )
        for dataset in ['new_listings', 'delistings', 'identifier_changes']:
            self.ingestion.ingest(sources[dataset])
        with self.assertRaisesRegex(ValueError, '차단'):
            self.ingestion.build_and_promote()
        self.assertIsNone(self.store.current_fingerprint())

    def test_rebuild_from_selected_raw_versions_reproduces_fingerprint(self):
        self.ingest_all()
        promoted = self.ingestion.build_and_promote()
        rebuilt = self.ingestion.rebuild()

        self.assertEqual(rebuilt['status'], 'passed')
        self.assertEqual(rebuilt['actual_fingerprint'], promoted['fingerprint'])
        record = self.store.rebuilds().iloc[-1]
        self.assertEqual(record.status, 'passed')
        self.assertEqual(record.parser_version, self.ingestion.PARSER_VERSION)
        self.assertEqual(record.rules_version, self.ingestion.RULES_VERSION)

    def test_rebuild_mismatch_keeps_previous_model_and_records_failure(self):
        self.ingest_all()
        promoted = self.ingestion.build_and_promote()
        changed = self.ingestion.ingest(official_sources('삼성전자 변경')['security_master'])
        self.ingestion.select_raw_version(
            'security_master', changed['raw_version_id'], 'KRX 공식 정정 공지 2026-10-03'
        )

        rebuilt = self.ingestion.rebuild()

        self.assertEqual(rebuilt['status'], 'failed')
        self.assertNotEqual(rebuilt['actual_fingerprint'], promoted['fingerprint'])
        self.assertEqual(self.store.current_fingerprint(), promoted['fingerprint'])
        record = self.store.rebuilds().iloc[-1]
        self.assertEqual(record.status, 'failed')
        self.assertIn('불일치', record.message)


if __name__ == '__main__':
    unittest.main()
