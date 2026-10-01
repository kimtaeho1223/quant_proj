import gzip
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantdesk.historical_market import (
    DailySourceResult,
    DailyValidationError,
    DateState,
    InvalidStateTransition,
    RawArchive,
    normalize_daily_market,
    transition_date_state,
    validate_daily_market,
)


class HistoricalMarketContractTests(unittest.TestCase):
    def test_source_result_keeps_safe_provenance_without_secret_query(self):
        result = DailySourceResult(
            requested_date='2026-09-25',
            source_type='data_go_kr',
            content=b'{"rows": []}',
            metadata={
                'endpoint': 'https://apis.data.go.kr/stock',
                'serviceKey': 'secret',
                'request_url': 'https://apis.data.go.kr/stock?serviceKey=secret&basDt=20260925',
            },
        )

        self.assertEqual(result.requested_date, '2026-09-25')
        self.assertEqual(result.content, b'{"rows": []}')
        self.assertEqual(result.safe_metadata, {
            'endpoint': 'https://apis.data.go.kr/stock',
            'request_url': 'https://apis.data.go.kr/stock',
        })
        self.assertNotIn('secret', repr(result))

    def test_date_state_allows_only_auditable_forward_transitions(self):
        self.assertEqual(
            transition_date_state(DateState.PENDING, DateState.DOWNLOADING),
            DateState.DOWNLOADING,
        )
        self.assertEqual(
            transition_date_state(DateState.DOWNLOADING, DateState.FAILED),
            DateState.FAILED,
        )
        self.assertEqual(
            transition_date_state(DateState.FAILED, DateState.DOWNLOADING),
            DateState.DOWNLOADING,
        )
        with self.assertRaises(InvalidStateTransition):
            transition_date_state(DateState.PENDING, DateState.PROMOTED)


class RawArchiveTests(unittest.TestCase):
    def source(self, content=b'official source bytes'):
        return DailySourceResult('2026-09-25', 'data_go_kr', content)

    def test_store_is_content_addressed_and_round_trips_exact_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = RawArchive(
                folder,
                clock=lambda: datetime(2026, 9, 26, 1, 2, 3, tzinfo=timezone.utc),
            )

            first = archive.store(self.source())
            repeated = archive.store(self.source())
            changed = archive.store(self.source(b'corrected source bytes'))

            self.assertEqual(first.path, repeated.path)
            self.assertNotEqual(first.path, changed.path)
            self.assertTrue(first.path.is_file())
            self.assertTrue(first.path.resolve().is_relative_to(Path(folder).resolve()))
            self.assertFalse(list(Path(folder).rglob('*.part')))
            with gzip.open(first.path, 'rb') as saved:
                self.assertEqual(saved.read(), b'official source bytes')

    def test_failed_atomic_replace_leaves_no_final_or_partial_file(self):
        def fail_replace(*_):
            raise OSError('disk unavailable')

        with tempfile.TemporaryDirectory() as folder:
            archive = RawArchive(folder, replace_func=fail_replace)

            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                archive.store(self.source())

            self.assertFalse(list(Path(folder).rglob('*.gz')))
            self.assertFalse(list(Path(folder).rglob('*.part')))


def official_rows():
    return pd.DataFrame([
        {
            'basDt': '20260925', 'srtnCd': '005930', 'itmsNm': '삼성전자',
            'mrktCtg': 'KOSPI', 'mkp': '', 'hipr': '71,000', 'lopr': '69,000',
            'clpr': '70,000', 'trqu': '0', 'trPrc': '0',
            'mrktTotAmt': '400,000,000', 'lstgStCnt': '5,969,782,550',
        },
        {
            'basDt': '20260925', 'srtnCd': 660, 'itmsNm': '테스트코스닥',
            'mrktCtg': 'KOSDAQ', 'mkp': '10,000', 'hipr': '10,500', 'lopr': '9,900',
            'clpr': '10,200', 'trqu': '100', 'trPrc': '1,020,000',
            'mrktTotAmt': '100,000,000', 'lstgStCnt': '9,803,921',
        },
        {
            'basDt': '20260925', 'srtnCd': '999999', 'itmsNm': '코넥스',
            'mrktCtg': 'KONEX', 'mkp': '1', 'hipr': '1', 'lopr': '1',
            'clpr': '1', 'trqu': '1', 'trPrc': '1',
            'mrktTotAmt': '1', 'lstgStCnt': '1',
        },
    ])


class DailyMarketNormalizationTests(unittest.TestCase):
    def test_official_rows_normalize_without_current_listing_join(self):
        normalized = normalize_daily_market(official_rows(), '2026-09-25')

        self.assertEqual(normalized.code.tolist(), ['005930', '000660'])
        self.assertEqual(normalized.market.tolist(), ['KOSPI', 'KOSDAQ'])
        self.assertTrue(pd.isna(normalized.iloc[0].open))
        self.assertEqual(normalized.iloc[0].close, 70_000)
        self.assertEqual(normalized.iloc[0].volume, 0)
        self.assertEqual(normalized.iloc[0].amount, 0)

    def test_hard_validation_rejects_bad_rows_but_allows_zero_trading(self):
        normalized = normalize_daily_market(official_rows(), '2026-09-25')
        self.assertEqual(validate_daily_market(normalized, '2026-09-25'), [])

        broken = normalized.copy()
        broken.loc[0, 'marcap'] = -1
        with self.assertRaisesRegex(DailyValidationError, '시가총액'):
            validate_daily_market(broken, '2026-09-25')

    def test_anomaly_rules_quarantine_instead_of_deleting_rows(self):
        normalized = normalize_daily_market(official_rows(), '2026-09-25')
        history = [{'row_count': 3, 'total_marcap': 625_000_000}] * 20

        findings = validate_daily_market(
            normalized,
            '2026-09-25',
            prior_summaries=history,
            changed_checksum=True,
            schema_changed=True,
        )

        self.assertEqual(
            {finding.rule_id for finding in findings},
            {'row_count_anomaly', 'marcap_anomaly', 'changed_checksum', 'schema_drift'},
        )
        self.assertTrue(all(finding.severity == 'quarantine' for finding in findings))
        self.assertEqual(len(normalized), 2)


if __name__ == '__main__':
    unittest.main()
