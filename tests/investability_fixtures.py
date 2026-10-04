from dataclasses import dataclass


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
