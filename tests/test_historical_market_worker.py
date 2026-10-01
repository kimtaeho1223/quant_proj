import tempfile
import unittest
from pathlib import Path

from quantdesk.historical_market_source import OfficialCsvSource
from quantdesk.historical_market_store import HistoricalMarketStore
from quantdesk.historical_market_worker import main
from tests.test_historical_market_ingestion import FakeSource, csv_bytes


class HistoricalMarketWorkerTests(unittest.TestCase):
    def test_missing_key_returns_configuration_error(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoricalMarketStore(Path(folder) / 'market.db')
            job_id = store.create_job('2026-09-25', '2026-09-25', ['2026-09-25'], 'api')

            code = main(
                ['--job-id', str(job_id), '--db', str(store.path), '--raw-root', str(Path(folder) / 'raw')],
                environ={},
            )

            self.assertEqual(code, 2)

    def test_worker_processes_job_and_releases_lease(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoricalMarketStore(Path(folder) / 'market.db')
            job_id = store.create_job('2026-09-25', '2026-09-25', ['2026-09-25'], 'api')
            result = OfficialCsvSource().from_bytes('2026-09-25', csv_bytes())

            code = main(
                ['--job-id', str(job_id), '--db', str(store.path), '--raw-root', str(Path(folder) / 'raw')],
                environ={'QUANTDESK_DATA_GO_KR_KEY': 'secret'},
                source_factory=lambda: FakeSource([result]),
            )

            self.assertEqual(code, 0)
            self.assertEqual(len(store.canonical_day('2026-09-25')), 2)
            with store.connect() as db:
                lease = db.execute('SELECT worker_token,heartbeat FROM historical_jobs WHERE id=?', (job_id,)).fetchone()
            self.assertEqual(lease, (None, None))

    def test_active_lease_prevents_second_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoricalMarketStore(Path(folder) / 'market.db')
            job_id = store.create_job('2026-09-25', '2026-09-25', ['2026-09-25'], 'api')
            from datetime import datetime, timedelta, timezone
            store.acquire_lease(job_id, 'already-running', datetime.now(timezone.utc), timedelta(minutes=2))

            code = main(
                ['--job-id', str(job_id), '--db', str(store.path), '--raw-root', str(Path(folder) / 'raw')],
                environ={'QUANTDESK_DATA_GO_KR_KEY': 'secret'},
            )

            self.assertEqual(code, 3)


if __name__ == '__main__':
    unittest.main()
