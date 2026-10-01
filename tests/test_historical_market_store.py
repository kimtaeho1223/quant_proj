import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantdesk.historical_market import ArchivedRawVersion, DateState, ValidationFinding
from quantdesk.historical_market_store import HistoricalMarketStore
from quantdesk.market import MarketStore
from tests.test_historical_market import official_rows
from quantdesk.historical_market import normalize_daily_market


class HistoricalMarketStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db_path = Path(self.folder.name) / 'market.db'
        self.store = HistoricalMarketStore(self.db_path)
        self.job_id = self.store.create_job(
            '2026-09-25', '2026-09-28', ['2026-09-25', '2026-09-28'], 'api',
        )

    def tearDown(self):
        self.folder.cleanup()

    def raw(self, sha='a' * 64):
        return ArchivedRawVersion(
            requested_date='2026-09-25',
            source_type='data_go_kr',
            sha256=sha,
            path=Path(self.folder.name) / f'{sha}.json.gz',
            fetched_at='2026-09-26T00:00:00+00:00',
        )

    def advance_to_validated(self):
        for state in [
            DateState.DOWNLOADING, DateState.DOWNLOADED,
            DateState.PARSED, DateState.VALIDATED,
        ]:
            self.store.transition_date(self.job_id, '2026-09-25', state)

    def test_job_raw_findings_and_canonical_day_round_trip(self):
        self.advance_to_validated()
        raw_id = self.store.record_raw_version(self.raw(), {'endpoint': 'official'})
        self.store.save_findings(raw_id, [
            ValidationFinding('warning', 'early_history', 'rolling history is incomplete'),
        ])
        day = normalize_daily_market(official_rows(), '2026-09-25')

        self.store.promote_day(self.job_id, '2026-09-25', raw_id, day)

        self.assertEqual(self.store.date_state(self.job_id, '2026-09-25'), DateState.PROMOTED)
        self.assertEqual(self.store.canonical_day('2026-09-25').code.tolist(), ['005930', '000660'])
        self.assertEqual(self.store.findings(raw_id)[0]['rule_id'], 'early_history')
        summary = self.store.job_summary(self.job_id)
        self.assertEqual(summary['promoted'], 1)
        self.assertEqual(summary['pending'], 1)

    def test_promotion_rolls_back_every_row_and_state_on_constraint_failure(self):
        self.advance_to_validated()
        raw_id = self.store.record_raw_version(self.raw())
        broken = normalize_daily_market(official_rows(), '2026-09-25')
        broken.loc[1, 'name'] = None

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.promote_day(self.job_id, '2026-09-25', raw_id, broken)

        self.assertTrue(self.store.canonical_day('2026-09-25').empty)
        self.assertEqual(self.store.date_state(self.job_id, '2026-09-25'), DateState.VALIDATED)

    def test_canonical_promotion_cannot_reach_engine_facing_tables(self):
        self.advance_to_validated()
        raw_id = self.store.record_raw_version(self.raw())
        self.store.promote_day(
            self.job_id, '2026-09-25', raw_id,
            normalize_daily_market(official_rows(), '2026-09-25'),
        )

        engine_store = MarketStore(self.db_path)
        self.assertTrue(engine_store.historical_snapshot('2026-09-25').empty)
        self.assertTrue(engine_store.prices('005930').empty)

    def test_worker_lease_prevents_duplicates_and_allows_stale_recovery(self):
        now = datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)
        self.assertTrue(self.store.acquire_lease(self.job_id, 'worker-a', now, timedelta(minutes=2)))
        self.assertFalse(self.store.acquire_lease(self.job_id, 'worker-b', now, timedelta(minutes=2)))
        self.assertTrue(self.store.acquire_lease(
            self.job_id, 'worker-b', now + timedelta(minutes=3), timedelta(minutes=2),
        ))


if __name__ == '__main__':
    unittest.main()
