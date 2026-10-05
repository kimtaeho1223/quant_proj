"""Conservative audit of one security's KRX trading-halt intervals."""

import csv
import gzip
import hashlib
import io
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantdesk.investability_source import (
    MAX_RAW_BYTES,
    InvestabilitySourceError,
    archive_investability_payload,
)


KRX_SUSPENSION_HISTORY_URL = (
    'https://data.krx.co.kr/contents/MDC/STAT/issue/MDCSTAT213.jsp'
)
_COLUMNS = {'종목코드', '종목명', '시장구분', '정지일', '재개일'}


def _day(value):
    try:
        return datetime.strptime(str(value).strip().replace('/', '-'), '%Y-%m-%d').date().isoformat()
    except ValueError as exc:
        raise ValueError('매매정지 이력의 날짜 형식이 올바르지 않습니다.') from exc


def parse_suspension_history(payload: bytes) -> pd.DataFrame:
    content = bytes(payload)
    if not content or len(content) > MAX_RAW_BYTES:
        raise ValueError('매매정지 CSV가 비었거나 허용 크기를 초과했습니다.')
    for encoding in ('utf-8-sig', 'utf-8', 'cp949'):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('매매정지 CSV 문자 인코딩을 읽을 수 없습니다.')
    reader = csv.DictReader(io.StringIO(decoded))
    if reader.fieldnames is None or not _COLUMNS.issubset(reader.fieldnames):
        raise ValueError('KRX 매매거래정지 내역의 필수 열이 없습니다.')
    records = []
    for number, row in enumerate(reader, start=2):
        if None in row or any(row.get(column) is None for column in _COLUMNS):
            raise ValueError(f'{number}행의 CSV 열 개수가 올바르지 않습니다.')
        code = row['종목코드'].strip()
        name = row['종목명'].strip()
        market = row['시장구분'].strip()
        if not re.fullmatch(r'\d{6}', code) or not name or market not in {'KOSPI', 'KOSDAQ'}:
            raise ValueError(f'{number}행의 종목 정보가 올바르지 않습니다.')
        halt = _day(row['정지일'])
        resume = _day(row['재개일']) if row['재개일'].strip() else None
        if resume and resume < halt:
            raise ValueError(f'{number}행의 재개일이 정지일보다 빠릅니다.')
        records.append({
            'halt_date': halt, 'resume_date': resume, 'short_code': code,
            'name': name, 'market': market, 'source_row_number': number,
        })
    if not records:
        raise ValueError('매매정지 CSV에 행이 없습니다.')
    frame = pd.DataFrame.from_records(records)
    if frame[['short_code', 'name', 'market']].drop_duplicates().shape[0] != 1:
        raise ValueError('한 파일에는 하나의 종목만 있어야 합니다.')
    frame = frame.sort_values('halt_date', kind='stable').reset_index(drop=True)
    for previous, current in zip(frame.iloc[:-1].itertuples(), frame.iloc[1:].itertuples()):
        if previous.resume_date is None or current.halt_date < previous.resume_date:
            raise ValueError('매매정지 구간이 중복되거나 겹칩니다.')
    return frame


def suspension_state_on(history: pd.DataFrame, date: str) -> str:
    """Never infer tradability outside a closed, dated halt interval."""
    day = _day(date)
    for row in history.itertuples():
        if row.resume_date == row.halt_date:
            continue
        if row.halt_date == day:
            return 'suspended'
        if row.resume_date is not None and row.halt_date < day < row.resume_date:
            return 'suspended'
        if row.resume_date == day:
            return 'resumption_event'
    return 'unknown'


def compare_suspension_timing(
    history: pd.DataFrame, *, interval_index: int,
    halted_disclosed_on: str, halted_disclosed_at: str,
    halted_effective_on: str, resumed_disclosed_on: str,
    resumed_disclosed_at: str, resumed_effective_on: str,
) -> dict:
    if not 0 <= interval_index < len(history):
        raise ValueError('매매정지 구간 번호가 올바르지 않습니다.')
    halted_disclosure = _day(halted_disclosed_on)
    halted_effective = _day(halted_effective_on)
    resumed_disclosure = _day(resumed_disclosed_on)
    resumed_effective = _day(resumed_effective_on)
    times = (halted_disclosed_at.strip(), resumed_disclosed_at.strip())
    for value in times:
        if value and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('공개 시각은 24시간제 HH:MM 형식으로 입력해 주세요.')
    row = history.iloc[interval_index]
    return {
        'effective_dates_match': (
            row.resume_date is not None and row.halt_date < row.resume_date
            and halted_effective == row.halt_date
            and resumed_effective == row.resume_date
        ),
        'calendar_order_valid': (
            halted_disclosure <= halted_effective
            < resumed_disclosure <= resumed_effective
        ),
        'publication_times_recorded': all(times),
        'publication_time_verified': False,
    }


def archive_suspension_history(root: Path, payload: bytes):
    history = parse_suspension_history(payload)
    try:
        return archive_investability_payload(
            root, 'suspension_history', history.halt_date.max(), payload,
            datetime.now(timezone.utc).isoformat(), KRX_SUSPENSION_HISTORY_URL,
        )
    except InvestabilitySourceError as exc:
        raise ValueError(str(exc)) from exc


def load_suspension_history(path: Path) -> pd.DataFrame:
    expected = Path(path).name.split('-')[-1].split('.')[0]
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('보존 원본 파일명의 체크섬이 올바르지 않습니다.')
    with gzip.open(path, 'rb') as source:
        payload = source.read(MAX_RAW_BYTES + 1)
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError('보존 원본의 SHA-256 체크섬이 일치하지 않습니다.')
    return parse_suspension_history(payload)
