import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.investability_fixtures import FakeResponse, STATUS_CSV, status_csv_bytes


class InvestabilitySourceTests(unittest.TestCase):
    def test_archive_is_checksum_addressed_and_does_not_overwrite_changed_bytes(self):
        from quantdesk.investability_source import archive_investability_payload

        with tempfile.TemporaryDirectory() as folder:
            first = archive_investability_payload(
                Path(folder), 'daily_status', '2026-10-02', status_csv_bytes(),
                '2026-10-03T00:00:00+00:00', 'https://data.krx.co.kr/status?secret=x',
            )
            repeat = archive_investability_payload(
                Path(folder), 'daily_status', '2026-10-02', status_csv_bytes(),
                '2026-10-04T00:00:00+00:00', 'https://data.krx.co.kr/status?secret=y',
            )
            changed = archive_investability_payload(
                Path(folder), 'daily_status', '2026-10-02',
                status_csv_bytes(STATUS_CSV.replace('삼성전자우', '삼성전자1우')),
                '2026-10-04T00:00:00+00:00', 'https://data.krx.co.kr/status',
            )

            self.assertEqual(first.path, repeat.path)
            self.assertNotEqual(first.sha256, changed.sha256)
            self.assertNotEqual(first.path, changed.path)
            self.assertEqual(gzip.open(first.path, 'rb').read(), status_csv_bytes())
            self.assertNotIn('secret', first.source_url)

    def test_archive_rejects_path_escape_and_oversized_payload(self):
        from quantdesk.investability_source import (
            InvestabilitySourceError,
            archive_investability_payload,
        )

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(InvestabilitySourceError):
                archive_investability_payload(
                    Path(folder), '../escape', '2026-10-02', b'x',
                    '2026-10-03T00:00:00+00:00', 'https://data.krx.co.kr',
                )
            with patch('quantdesk.investability_source.MAX_RAW_BYTES', 3):
                with self.assertRaisesRegex(InvestabilitySourceError, '크기'):
                    archive_investability_payload(
                        Path(folder), 'daily_status', '2026-10-02', b'xxxx',
                        '2026-10-03T00:00:00+00:00', 'https://data.krx.co.kr',
                    )

    def test_parser_normalizes_official_korean_and_krx_column_aliases(self):
        from quantdesk.investability_source import (
            decode_investability_csv,
            normalize_investability_rows,
        )

        frame = decode_investability_csv(status_csv_bytes(encoding='cp949'))
        rows = normalize_investability_rows(
            frame, 'daily_status', '2026-10-02', 7,
            '2026-10-03T00:00:00+00:00',
        )

        self.assertEqual(rows.short_code.tolist(), ['005930', '005935'])
        self.assertEqual(rows.market.tolist(), ['KOSPI', 'KOSPI'])
        self.assertEqual(rows.security_kind.tolist(), ['common', 'preferred'])
        self.assertEqual(rows.management_designation.tolist(), [False, True])
        self.assertEqual(rows.trading_suspension.tolist(), [False, False])
        self.assertEqual(rows.liquidation_trading.tolist(), [False, False])

    def test_parser_preserves_source_row_and_observed_at(self):
        from quantdesk.investability_source import (
            decode_investability_csv,
            normalize_investability_rows,
        )

        rows = normalize_investability_rows(
            decode_investability_csv(status_csv_bytes()),
            'daily_status', '2026-10-02', 9, '2026-10-03T01:02:03+00:00',
        )

        self.assertEqual(rows.raw_version_id.tolist(), [9, 9])
        self.assertEqual(rows.source_row_number.tolist(), [1, 2])
        self.assertEqual(rows.observed_at.unique().tolist(), ['2026-10-03T01:02:03+00:00'])

    def test_parser_rejects_empty_or_wrong_date_snapshot(self):
        from quantdesk.investability_source import (
            InvestabilitySourceError,
            decode_investability_csv,
            normalize_investability_rows,
        )

        with self.assertRaises(InvestabilitySourceError):
            decode_investability_csv(b'')
        frame = decode_investability_csv(status_csv_bytes())
        with self.assertRaisesRegex(InvestabilitySourceError, '기준일'):
            normalize_investability_rows(
                frame, 'daily_status', '2026-10-01', 1,
                '2026-10-03T00:00:00+00:00',
            )

    def test_parser_treats_csv_formula_cells_as_plain_text(self):
        from quantdesk.investability_source import decode_investability_csv

        payload = status_csv_bytes(STATUS_CSV.replace('삼성전자', '=1+1', 1))
        frame = decode_investability_csv(payload)

        self.assertEqual(frame['종목명'].iloc[0], '=1+1')

    def test_fetch_surfaces_login_and_authentication_failures_without_secrets(self):
        from quantdesk.investability_source import (
            InvestabilitySourceError,
            fetch_investability_snapshot,
        )

        def http_get(url, params, headers, timeout):
            self.assertEqual(params['date'], '20261002')
            return FakeResponse(b'LOGOUT', 400)

        with self.assertRaises(InvestabilitySourceError) as caught:
            fetch_investability_snapshot('2026-10-02', http_get, 'very-secret')

        self.assertIn('공식 CSV', str(caught.exception))
        self.assertNotIn('very-secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
