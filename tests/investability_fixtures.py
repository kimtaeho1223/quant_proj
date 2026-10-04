from dataclasses import dataclass

import pandas as pd


STATUS_CSV = """기준일,표준코드,단축코드,종목명,시장구분,주식종류,관리종목여부,거래정지여부,정리매매여부,적용일,공시일시
20261002,KR7005930003,005930,삼성전자,KOSPI,보통주,N,N,N,2026-10-02,2026-10-01 18:00:00+09:00
20261002,KR7005931001,005935,삼성전자우,KOSPI,우선주,Y,N,N,2026-10-02,2026-10-01 18:00:00+09:00
"""


@dataclass
class FakeResponse:
    content: bytes
    status_code: int = 200

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


def status_csv_bytes(text=STATUS_CSV, encoding='utf-8-sig'):
    return text.encode(encoding)


class FakeLifecycleReader:
    def __init__(self, intervals=None):
        self.intervals = intervals if intervals is not None else pd.DataFrame([
            dict(security_id='sec-samsung', standard_code='KR7005930003',
                 short_code='005930', name='삼성전자', market='KOSPI',
                 security_kind='common', valid_from='1975-06-11', valid_to=None),
            dict(security_id='sec-preferred', standard_code='KR7005931001',
                 short_code='005935', name='삼성전자우', market='KOSPI',
                 security_kind='preferred', valid_from='1975-06-11', valid_to=None),
        ])

    def listed_securities(self, as_of_date):
        day = pd.Timestamp(as_of_date).date().isoformat()
        frame = self.intervals
        return frame[
            frame.valid_from.le(day)
            & (frame.valid_to.isna() | frame.valid_to.gt(day))
        ].copy().reset_index(drop=True)


def status_rows(dates=('2026-10-01', '2026-10-02'), suspended=False):
    records = []
    for index, day in enumerate(dates, start=1):
        records.append(dict(
            dataset='daily_status', requested_date=day, raw_version_id=index,
            source_row_number=1, observed_at=f'{day}T10:00:00+09:00',
            standard_code='KR7005930003', short_code='005930', name='삼성전자',
            market='KOSPI', security_kind='common', management_designation=False,
            trading_suspension=bool(suspended), liquidation_trading=False,
            effective_date=day, published_at=f'{day}T08:00:00+09:00',
        ))
    return pd.DataFrame(records)


def sessions(*dates):
    return pd.DataFrame({'date': list(dates)})
