import tempfile
import unittest
from pathlib import Path

from quantdesk.historical_market import (
    DailySourceResult,
    DateState,
    HistoricalIngestionService,
    RawArchive,
)
from quantdesk.historical_market_source import OfficialCsvSource
from quantdesk.historical_market_store import HistoricalMarketStore


def csv_bytes(close=105, day='20260925'):
    return (
        '기준일자,종목코드,종목명,시장구분,시가,고가,저가,종가,거래량,거래대금,시가총액,상장주식수\n'
        f'{day},005930,삼성전자,KOSPI,100,110,90,{close},10,1050,400000000,1000\n'
        f'{day},000660,테스트,KOSDAQ,100,110,90,{close},10,1050,100000000,1000\n'
    ).encode('utf-8')


class FakeSource:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def fetch(self, day, _key):
        self.calls.append(day)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class HistoricalMarketIngestionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.store = HistoricalMarketStore(root / 'market.db')
        self.archive = RawArchive(root / 'raw')

    def tearDown(self):
        self.folder.cleanup()

    def result(self, content=None, day='2026-09-25'):
        return OfficialCsvSource().from_bytes(day, content or csv_bytes(day=day.replace('-', '')))

    def test_job_excludes_weekends_and_promotes_valid_day_once(self):
        source = FakeSource([self.result(), self.result(day='2026-09-28')])
        service = HistoricalIngestionService(self.store, self.archive, source, service_key='key')
        job_id = service.create_job('2026-09-25', '2026-09-28')

        service.process_next(job_id)
        service.process_next(job_id)

        dates = self.store.job_dates(job_id)
        self.assertEqual(dates.trade_date.tolist(), ['2026-09-25', '2026-09-28'])
        self.assertEqual(dates.iloc[0].state, DateState.PROMOTED.value)
        self.assertEqual(source.calls, ['2026-09-25', '2026-09-28'])

    def test_empty_weekday_retries_three_times_then_stays_unresolved(self):
        empty = DailySourceResult(
            '2026-09-25', 'data_go_kr', b'{"pages": []}',
            {'non_session_candidate': True},
        )
        source = FakeSource([empty, empty, empty])
        service = HistoricalIngestionService(
            self.store, self.archive, source, service_key='key', sleeper=lambda _: None,
        )
        job_id = service.create_job('2026-09-25', '2026-09-25')

        service.process_next(job_id)
        service.process_next(job_id)
        service.process_next(job_id)

        self.assertEqual(
            self.store.date_state(job_id, '2026-09-25'),
            DateState.CALENDAR_UNRESOLVED,
        )
        self.assertEqual(len(source.calls), 3)

    def test_changed_official_version_is_quarantined_not_overwritten(self):
        first = self.result()
        source = FakeSource([first])
        service = HistoricalIngestionService(self.store, self.archive, source, service_key='key')
        job_id = service.create_job('2026-09-25', '2026-09-25')
        service.process_next(job_id)
        original = self.store.canonical_day('2026-09-25').close.tolist()

        changed = self.result(csv_bytes(close=106))
        source.results.append(changed)
        second_job = service.create_job('2026-09-25', '2026-09-25')
        service.process_next(second_job)

        self.assertEqual(self.store.date_state(second_job, '2026-09-25'), DateState.QUARANTINED)
        self.assertEqual(self.store.canonical_day('2026-09-25').close.tolist(), original)

    def test_pause_is_persisted_and_blocks_processing(self):
        source = FakeSource([self.result()])
        service = HistoricalIngestionService(self.store, self.archive, source, service_key='key')
        job_id = service.create_job('2026-09-25', '2026-09-25')
        service.request_pause(job_id)

        self.assertIsNone(service.process_next(job_id))
        self.assertEqual(source.calls, [])
        service.resume(job_id)
        service.process_next(job_id)
        self.assertEqual(self.store.date_state(job_id, '2026-09-25'), DateState.PROMOTED)


if __name__ == '__main__':
    unittest.main()
