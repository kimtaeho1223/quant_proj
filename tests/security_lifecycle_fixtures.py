from quantdesk.security_lifecycle_source import LifecycleSourceResult
import pandas as pd


MASTER_CSV = """표준코드,단축코드,한글 종목명,시장구분,증권구분,주식종류,상장일,회사코드
KR7005930003,005930,삼성전자,KOSPI,주권,보통주,1975/06/11,C001
KR7005931001,005935,삼성전자우,KOSPI,주권,우선주,1975/06/11,C001
"""


def lifecycle_result(dataset='security_master', content=None, **metadata):
    return LifecycleSourceResult(
        dataset=dataset,
        scope_start='2022-01-01',
        scope_end='2026-12-31',
        content=content if content is not None else MASTER_CSV.encode('utf-8-sig'),
        metadata={'source_url': 'https://data.krx.co.kr/official', **metadata},
    )


def normalized_lifecycle_rows():
    return pd.DataFrame([
        dict(dataset='security_master', raw_version_id=1, source_row_number=1,
             observed_at='2026-10-03T00:00:00+00:00', standard_code='KR7005930003',
             short_code='005930', name='삼성전자', market='KOSPI', security_type='주권',
             stock_type='보통주', listing_date='1975-06-11', last_trading_date=None,
             delisting_date=None, issuer_reference='C001', event_type='listing',
             effective_date='1975-06-11', previous_code=None, previous_name=None,
             previous_market=None, successor_standard_code=None),
        dict(dataset='security_master', raw_version_id=1, source_row_number=2,
             observed_at='2026-10-03T00:00:00+00:00', standard_code='KR7005931001',
             short_code='005935', name='삼성전자우', market='KOSPI', security_type='주권',
             stock_type='우선주', listing_date='1975-06-11', last_trading_date=None,
             delisting_date=None, issuer_reference='C001', event_type='listing',
             effective_date='1975-06-11', previous_code=None, previous_name=None,
             previous_market=None, successor_standard_code=None),
        dict(dataset='delistings', raw_version_id=2, source_row_number=1,
             observed_at='2026-10-03T00:00:00+00:00', standard_code='KR7000120006',
             short_code='000120', name='과거종목', market='KOSPI', security_type='주권',
             stock_type='보통주', listing_date='2020-01-02', last_trading_date='2024-06-27',
             delisting_date='2024-07-01', issuer_reference='C002', event_type='delisting',
             effective_date='2024-07-01', previous_code=None, previous_name=None,
             previous_market=None, successor_standard_code=None),
    ])

