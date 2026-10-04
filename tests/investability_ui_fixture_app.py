import os
from pathlib import Path

import pandas as pd

from quantdesk.investability_store import InvestabilityStore
from quantdesk.investability_ui import render_investability_audit
from tests.investability_fixtures import FakeLifecycleReader, sessions


class FixtureLifecycleReader(FakeLifecycleReader):
    def readiness(self, start_date, end_date):
        return {
            'lifecycle_ready': True,
            'fingerprint': 'fixture-lifecycle',
            'blocking_reasons': [],
        }

    def current_fingerprint(self):
        return 'fixture-lifecycle'


class FixtureDailyMarketReader:
    def canonical_day(self, trade_date):
        return pd.DataFrame(columns=[
            'trade_date', 'code', 'name', 'market', 'close', 'volume',
            'raw_version_id',
        ])


render_investability_audit(
    InvestabilityStore(os.environ['QUANTDESK_MARKET_DB']),
    Path(os.environ['QUANTDESK_INVESTABILITY_RAW_ROOT']),
    FixtureLifecycleReader(),
    sessions('2026-10-02', '2026-10-05'),
    FixtureDailyMarketReader(),
)
