"""Background entry point for resumable historical market collection."""

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

from quantdesk.historical_market import HistoricalIngestionService, RawArchive
from quantdesk.historical_market_source import DataGoKrDailySource
from quantdesk.historical_market_store import HistoricalMarketStore


def _parser():
    parser = argparse.ArgumentParser(description='Collect historical Korean daily market data.')
    parser.add_argument('--job-id', required=True, type=int)
    parser.add_argument('--db', required=True)
    parser.add_argument('--raw-root', required=True)
    return parser


def main(argv=None, environ=None, source_factory=DataGoKrDailySource):
    args = _parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    service_key = environ.get('QUANTDESK_DATA_GO_KR_KEY', '')
    if not service_key:
        print('공공데이터포털 인증키가 필요합니다.', file=sys.stderr)
        return 2

    store = HistoricalMarketStore(args.db)
    token = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    if not store.acquire_lease(args.job_id, token, now, timedelta(minutes=2)):
        print('같은 수집 작업이 이미 실행 중입니다.', file=sys.stderr)
        return 3

    try:
        service = HistoricalIngestionService(
            store,
            RawArchive(args.raw_root),
            source_factory(),
            service_key=service_key,
        )
        service.run(args.job_id, on_step=lambda: store.heartbeat(args.job_id, token))
        return 0
    except Exception:
        print('과거 시장 데이터 수집 작업이 중단되었습니다. 날짜별 오류를 확인해 주세요.', file=sys.stderr)
        return 1
    finally:
        store.release_lease(args.job_id, token)


if __name__ == '__main__':
    raise SystemExit(main())
