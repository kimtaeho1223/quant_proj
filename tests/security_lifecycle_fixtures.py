from quantdesk.security_lifecycle_source import LifecycleSourceResult


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

