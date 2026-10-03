import gzip
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.security_lifecycle_fixtures import MASTER_CSV, lifecycle_result
from quantdesk.security_lifecycle_source import (
    KrxLifecycleSource,
    LifecycleRawArchive,
    LifecycleSourceError,
    OfficialLifecycleCsvSource,
    decode_lifecycle_source,
)


class FakeResponse:
    def __init__(self, content, status_code=200, content_type='text/csv'):
        self.content = content
        self.status_code = status_code
        self.headers = {'Content-Type': content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, data, headers, timeout):
        self.calls.append((url, data, headers, timeout))
        return self.response


class SecurityLifecycleSourceTests(unittest.TestCase):
    def test_csv_decoding_preserves_codes_for_utf8_bom_and_cp949(self):
        source = OfficialLifecycleCsvSource()
        for encoding in ('utf-8-sig', 'cp949'):
            result = source.from_bytes('security_master', MASTER_CSV.encode(encoding))
            frame = decode_lifecycle_source(result)
            self.assertEqual(frame['단축코드'].tolist(), ['005930', '005935'])
            self.assertEqual(frame['표준코드'].iloc[0], 'KR7005930003')

    def test_archive_is_atomic_idempotent_and_never_persists_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = LifecycleRawArchive(
                folder, clock=lambda: datetime(2026, 10, 3, tzinfo=timezone.utc),
            )
            result = lifecycle_result(service_key='secret', request_url='https://x.test/a?key=secret')
            first = archive.store(result)
            second = archive.store(result)

            self.assertEqual(first.path, second.path)
            self.assertEqual(gzip.open(first.path, 'rb').read(), result.content)
            self.assertNotIn('secret', str(result.safe_metadata))
            self.assertFalse(list(Path(folder).rglob('*.part')))

    def test_same_scope_with_changed_payload_creates_a_second_raw_version(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = LifecycleRawArchive(folder)
            first = archive.store(lifecycle_result())
            second = archive.store(lifecycle_result(content=MASTER_CSV.replace('삼성전자우', '삼성전자1우').encode('utf-8')))
            self.assertNotEqual(first.sha256, second.sha256)
            self.assertNotEqual(first.path, second.path)

    def test_official_source_rejects_html_empty_and_partial_downloads(self):
        source = OfficialLifecycleCsvSource()
        for content in (b'', b'<html>login</html>', '표준코드,단축코드\n'.encode('utf-8')):
            with self.subTest(content=content[:10]):
                with self.assertRaises(LifecycleSourceError):
                    decode_lifecycle_source(source.from_bytes('security_master', content))

    def test_krx_source_maps_each_supported_dataset_to_official_metadata(self):
        for dataset in ('security_master', 'new_listings', 'delistings', 'identifier_changes'):
            session = FakeSession(FakeResponse(MASTER_CSV.encode('utf-8-sig')))
            result = KrxLifecycleSource(session=session).fetch(
                dataset, '2022-01-01', '2026-12-31',
            )
            self.assertEqual(result.dataset, dataset)
            self.assertEqual(result.safe_metadata['provider'], 'KRX')
            self.assertIn('official_screen', result.safe_metadata)
            self.assertTrue(session.calls[0][1]['bld'].startswith('dbms/'))

    def test_krx_logout_response_explains_official_csv_fallback(self):
        session = FakeSession(FakeResponse(b'LOGOUT', status_code=400))

        with self.assertRaisesRegex(LifecycleSourceError, '공식 CSV'):
            KrxLifecycleSource(session=session).fetch('security_master')


if __name__ == '__main__':
    unittest.main()
