"""Audit KRX management-designation events without promoting daily coverage."""

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


KRX_MANAGEMENT_HISTORY_URL = (
    'https://data.krx.co.kr/contents/MDC/STAT/issue/MDCSTAT215.jsp'
)
_COLUMNS = {'일자', '종목코드', '종목명', '시장구분', '지정구분', '사유'}
_EVENTS = {'지정', '해제', '-'}


def _day(value):
    try:
        return datetime.strptime(str(value).strip().replace('/', '-'), '%Y-%m-%d').date().isoformat()
    except ValueError as exc:
        raise ValueError('관리종목 내역의 날짜 형식이 올바르지 않습니다.') from exc


def parse_management_history(payload: bytes) -> pd.DataFrame:
    """Read one stock's unchanged KRX CSV; '-' means no event on that row."""
    content = bytes(payload)
    if not content or len(content) > MAX_RAW_BYTES:
        raise ValueError('관리종목 CSV가 비었거나 허용 크기를 초과했습니다.')
    for encoding in ('utf-8-sig', 'utf-8', 'cp949'):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('관리종목 CSV 문자 인코딩을 읽을 수 없습니다.')
    reader = csv.DictReader(io.StringIO(decoded))
    if reader.fieldnames is None or not _COLUMNS.issubset(reader.fieldnames):
        raise ValueError('KRX 관리종목 변경 내역의 필수 열이 없습니다.')
    records = []
    for number, row in enumerate(reader, start=2):
        if None in row or any(row.get(column) is None for column in _COLUMNS):
            raise ValueError(f'{number}행의 CSV 열 개수가 올바르지 않습니다.')
        event = row['지정구분'].strip()
        code = row['종목코드'].strip()
        name = row['종목명'].strip()
        market = row['시장구분'].strip()
        if event not in _EVENTS or not re.fullmatch(r'\d{6}', code):
            raise ValueError(f'{number}행의 종목코드 또는 지정구분이 올바르지 않습니다.')
        if not name or market not in {'KOSPI', 'KOSDAQ'}:
            raise ValueError(f'{number}행의 종목명 또는 시장구분이 올바르지 않습니다.')
        records.append({
            'date': _day(row['일자']), 'short_code': code, 'name': name,
            'market': market, 'event': event, 'reason': row['사유'].strip(),
            'source_row_number': number,
        })
    if not records:
        raise ValueError('관리종목 CSV에 행이 없습니다.')
    frame = pd.DataFrame.from_records(records)
    if frame[['short_code', 'name', 'market']].drop_duplicates().shape[0] != 1:
        raise ValueError('한 파일에는 하나의 종목만 있어야 합니다.')
    if frame.date.duplicated().any():
        raise ValueError('같은 날짜의 관리종목 내역이 중복됩니다.')
    if not frame.event.isin({'지정', '해제'}).any():
        raise ValueError('관리종목 지정·해제 사건이 없습니다.')
    return frame.sort_values('date', kind='stable').reset_index(drop=True)


def management_state_on(history: pd.DataFrame, date: str) -> str:
    """Report event-derived status only on dates covered by this export."""
    day = _day(date)
    if day not in set(history.date):
        return 'unknown'
    events = history[history.date.le(day) & history.event.isin({'지정', '해제'})]
    if events.empty:
        return 'unknown'
    return 'designated' if events.iloc[-1].event == '지정' else 'released'


def compare_management_timing(
    history: pd.DataFrame, *,
    designated_disclosed_on: str, designated_disclosed_at: str,
    designated_effective_on: str, released_disclosed_on: str,
    released_disclosed_at: str, released_effective_on: str,
) -> dict:
    designated_disclosure = _day(designated_disclosed_on)
    designated_effective = _day(designated_effective_on)
    released_disclosure = _day(released_disclosed_on)
    released_effective = _day(released_effective_on)
    times = (designated_disclosed_at.strip(), released_disclosed_at.strip())
    for value in times:
        if value and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('공개 시각은 24시간제 HH:MM 형식으로 입력해 주세요.')
    events = history[history.event.isin({'지정', '해제'})]
    pairs = list(zip(events.date, events.event))
    return {
        'effective_dates_match': (
            designated_effective < released_effective
            and (designated_effective, '지정') in pairs
            and (released_effective, '해제') in pairs
        ),
        'calendar_order_valid': (
            designated_disclosure <= designated_effective
            < released_disclosure <= released_effective
        ),
        'publication_times_recorded': all(times),
        'publication_time_verified': False,
    }


def archive_management_history(root: Path, payload: bytes):
    history = parse_management_history(payload)
    try:
        return archive_investability_payload(
            root, 'management_history', history.date.max(), payload,
            datetime.now(timezone.utc).isoformat(),
            KRX_MANAGEMENT_HISTORY_URL,
        )
    except InvestabilitySourceError as exc:
        raise ValueError(str(exc)) from exc


def load_management_history(path: Path) -> pd.DataFrame:
    name = Path(path).name
    expected = name.split('-')[-1].split('.')[0]
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('보존 원본 파일명의 체크섬이 올바르지 않습니다.')
    with gzip.open(path, 'rb') as source:
        payload = source.read()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError('보존 원본의 SHA-256 체크섬이 일치하지 않습니다.')
    return parse_management_history(payload)
