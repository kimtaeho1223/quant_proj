"""Auditable acquisition primitives for point-in-time daily market data."""

import gzip
import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from statistics import median
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

import pandas as pd


class HistoricalMarketError(ValueError):
    """Base error for historical market acquisition."""


class InvalidStateTransition(HistoricalMarketError):
    """Raised when a date state skips required audit stages."""


class DailyValidationError(HistoricalMarketError):
    """Raised when one daily source cannot be accepted structurally."""


class DateState(StrEnum):
    PENDING = 'pending'
    DOWNLOADING = 'downloading'
    DOWNLOADED = 'downloaded'
    PARSED = 'parsed'
    VALIDATED = 'validated'
    PROMOTED = 'promoted'
    FAILED = 'failed'
    QUARANTINED = 'quarantined'
    CALENDAR_UNRESOLVED = 'calendar_unresolved'
    NON_SESSION = 'non_session'


_ALLOWED_TRANSITIONS = {
    DateState.PENDING: {DateState.DOWNLOADING},
    DateState.DOWNLOADING: {
        DateState.DOWNLOADED, DateState.FAILED, DateState.CALENDAR_UNRESOLVED,
    },
    DateState.DOWNLOADED: {
        DateState.PARSED, DateState.FAILED, DateState.QUARANTINED,
        DateState.CALENDAR_UNRESOLVED,
    },
    DateState.PARSED: {
        DateState.VALIDATED, DateState.FAILED, DateState.QUARANTINED,
    },
    DateState.VALIDATED: {DateState.PROMOTED, DateState.QUARANTINED, DateState.FAILED},
    DateState.FAILED: {DateState.DOWNLOADING},
    DateState.QUARANTINED: {DateState.DOWNLOADING},
    DateState.CALENDAR_UNRESOLVED: {DateState.DOWNLOADING, DateState.NON_SESSION},
    DateState.NON_SESSION: {DateState.DOWNLOADING},
    DateState.PROMOTED: {DateState.QUARANTINED},
}


def transition_date_state(current, target):
    current = DateState(current)
    target = DateState(target)
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidStateTransition(f'{current.value}에서 {target.value}(으)로 변경할 수 없습니다.')
    return target


def _safe_metadata(metadata):
    secret_names = {'servicekey', 'service_key', 'authorization', 'api_key', 'apikey'}
    safe = {}
    for key, value in dict(metadata or {}).items():
        if str(key).lower() in secret_names:
            continue
        if isinstance(value, str) and str(key).lower() in {'url', 'request_url'}:
            parts = urlsplit(value)
            value = urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))
        safe[str(key)] = value
    return safe


@dataclass(frozen=True)
class DailySourceResult:
    requested_date: str
    source_type: str
    content: bytes = field(repr=False)
    metadata: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        object.__setattr__(self, 'metadata', MappingProxyType(_safe_metadata(self.metadata)))

    @property
    def safe_metadata(self):
        return dict(self.metadata)


@dataclass(frozen=True)
class ArchivedRawVersion:
    requested_date: str
    source_type: str
    sha256: str
    path: Path
    fetched_at: str


@dataclass(frozen=True)
class ValidationFinding:
    severity: str
    rule_id: str
    message: str
    observed: float | int | str | None = None
    threshold: float | int | str | None = None


class RawArchive:
    def __init__(self, root, clock=None, replace_func=None):
        self.root = Path(root).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.replace_func = replace_func or os.replace

    def store(self, result):
        try:
            requested = datetime.strptime(result.requested_date, '%Y-%m-%d').date()
        except ValueError as exc:
            raise HistoricalMarketError('원본 기준일 형식이 올바르지 않습니다.') from exc
        if not re.fullmatch(r'[a-z0-9_]+', result.source_type):
            raise HistoricalMarketError('원본 출처 이름이 올바르지 않습니다.')

        digest = hashlib.sha256(result.content).hexdigest()
        folder = (self.root / f'{requested.year:04d}' / result.requested_date).resolve()
        if not folder.is_relative_to(self.root):
            raise HistoricalMarketError('원본 저장 경로가 허용 범위를 벗어났습니다.')
        folder.mkdir(parents=True, exist_ok=True)

        matches = list(folder.glob(f'*-{digest}.{result.source_type}.gz'))
        if matches:
            target = matches[0]
            fetched = target.name.split('-', 1)[0]
            return ArchivedRawVersion(
                result.requested_date, result.source_type, digest, target, fetched,
            )

        fetched_at = self.clock().astimezone(timezone.utc)
        stamp = fetched_at.strftime('%Y%m%dT%H%M%SZ')
        target = (folder / f'{stamp}-{digest}.{result.source_type}.gz').resolve()
        if not target.is_relative_to(self.root):
            raise HistoricalMarketError('원본 저장 경로가 허용 범위를 벗어났습니다.')

        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='wb', dir=folder, prefix='.download-', suffix='.part', delete=False,
            ) as output:
                temporary = Path(output.name)
                with gzip.GzipFile(fileobj=output, mode='wb', mtime=0) as compressed:
                    compressed.write(result.content)
                output.flush()
                os.fsync(output.fileno())
            self.replace_func(temporary, target)
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise

        return ArchivedRawVersion(
            result.requested_date,
            result.source_type,
            digest,
            target,
            fetched_at.isoformat(),
        )


_OFFICIAL_COLUMNS = {
    'basDt': 'trade_date',
    'srtnCd': 'code',
    'itmsNm': 'name',
    'mrktCtg': 'market',
    'mkp': 'open',
    'hipr': 'high',
    'lopr': 'low',
    'clpr': 'close',
    'trqu': 'volume',
    'trPrc': 'amount',
    'mrktTotAmt': 'marcap',
    'lstgStCnt': 'listed_shares',
}

_KOREAN_COLUMNS = {
    '기준일자': 'trade_date',
    '종목코드': 'code',
    '단축코드': 'code',
    '종목명': 'name',
    '시장구분': 'market',
    '소속부': 'segment',
    '시가': 'open',
    '고가': 'high',
    '저가': 'low',
    '종가': 'close',
    '거래량': 'volume',
    '거래대금': 'amount',
    '시가총액': 'marcap',
    '상장주식수': 'listed_shares',
}

_CANONICAL_COLUMNS = [
    'trade_date', 'code', 'name', 'market', 'segment',
    'open', 'high', 'low', 'close', 'volume', 'amount', 'marcap', 'listed_shares',
]


def _normalize_code(value):
    if pd.isna(value):
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(6)


def normalize_daily_market(frame, requested_date):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise DailyValidationError('일별 전체시장 자료가 비어 있습니다.')
    mapping = _OFFICIAL_COLUMNS if set(_OFFICIAL_COLUMNS).issubset(frame.columns) else _KOREAN_COLUMNS
    renamed = frame.rename(columns=mapping).copy()
    required = set(_CANONICAL_COLUMNS) - {'segment'}
    if not required.issubset(renamed.columns):
        missing = ', '.join(sorted(required - set(renamed.columns)))
        raise DailyValidationError(f'일별 전체시장 필수 열이 없습니다: {missing}')
    if 'segment' not in renamed:
        renamed['segment'] = ''
    data = renamed[_CANONICAL_COLUMNS].copy()
    parsed_dates = pd.to_datetime(data.trade_date.astype(str), errors='coerce')
    data['trade_date'] = parsed_dates.dt.strftime('%Y-%m-%d')
    data['code'] = data.code.map(_normalize_code)
    data['name'] = data.name.astype(str).str.strip()
    data['market'] = data.market.astype(str).str.strip().str.upper()
    data['segment'] = data.segment.fillna('').astype(str).str.strip()
    numeric = ['open', 'high', 'low', 'close', 'volume', 'amount', 'marcap', 'listed_shares']
    for column in numeric:
        values = data[column].astype(str).str.replace(',', '', regex=False).str.strip()
        values = values.replace({'': None, 'None': None, 'nan': None})
        data[column] = pd.to_numeric(values, errors='coerce')
    data = data[data.market.isin(['KOSPI', 'KOSDAQ'])].reset_index(drop=True)
    validate_daily_market(data, requested_date)
    return data


def validate_daily_market(frame, requested_date, prior_summaries=(),
                          changed_checksum=False, schema_changed=False):
    required = set(_CANONICAL_COLUMNS)
    if not isinstance(frame, pd.DataFrame) or frame.empty or not required.issubset(frame.columns):
        raise DailyValidationError('일별 전체시장 필수 열 또는 행이 없습니다.')
    if frame.trade_date.isna().any() or set(frame.trade_date) != {requested_date}:
        raise DailyValidationError('요청일과 원본 기준일이 일치하지 않습니다.')
    if not frame.code.str.fullmatch(r'[0-9A-Z]{6}').all():
        raise DailyValidationError('종목코드 형식이 올바르지 않습니다.')
    if frame.code.duplicated().any():
        raise DailyValidationError('같은 기준일에 중복 종목코드가 있습니다.')
    if set(frame.market) != {'KOSPI', 'KOSDAQ'}:
        raise DailyValidationError('KOSPI 또는 KOSDAQ 시장 자료가 누락되었습니다.')
    if frame.name.eq('').any():
        raise DailyValidationError('종목명이 비어 있습니다.')

    positive = ['close', 'marcap', 'listed_shares']
    non_negative = ['volume', 'amount']
    optional_positive = ['open', 'high', 'low']
    if frame[positive].isna().any().any() or (frame[positive] <= 0).any().any():
        labels = {'close': '종가', 'marcap': '시가총액', 'listed_shares': '상장주식수'}
        broken = next(
            column for column in positive
            if frame[column].isna().any() or (frame[column] <= 0).any()
        )
        raise DailyValidationError(f'{labels[broken]} 값이 유효하지 않습니다.')
    if frame[non_negative].isna().any().any() or (frame[non_negative] < 0).any().any():
        raise DailyValidationError('거래량 또는 거래대금 값이 유효하지 않습니다.')
    if any((frame[column].dropna() <= 0).any() for column in optional_positive):
        raise DailyValidationError('시가·고가·저가 값이 유효하지 않습니다.')

    findings = []
    history = list(prior_summaries)
    if len(history) >= 20:
        expected_rows = median(float(item['row_count']) for item in history[-20:])
        ratio = len(frame) / expected_rows if expected_rows else 0
        if ratio < 0.9 or ratio > 1.1:
            findings.append(ValidationFinding(
                'quarantine', 'row_count_anomaly', '최근 거래일 대비 종목 수가 급변했습니다.',
                len(frame), f'{expected_rows * 0.9:.1f}..{expected_rows * 1.1:.1f}',
            ))
        previous_marcap = float(history[-1]['total_marcap'])
        current_marcap = float(frame.marcap.sum())
        change = abs(current_marcap / previous_marcap - 1) if previous_marcap else float('inf')
        if change >= 0.2 - 1e-12:
            findings.append(ValidationFinding(
                'quarantine', 'marcap_anomaly', '직전 거래일 대비 전체 시가총액이 급변했습니다.',
                current_marcap, '20%',
            ))
    if changed_checksum:
        findings.append(ValidationFinding(
            'quarantine', 'changed_checksum', '같은 날짜의 원본 체크섬이 변경되었습니다.',
        ))
    if schema_changed:
        findings.append(ValidationFinding(
            'quarantine', 'schema_drift', '공식 제공처의 열 구조가 변경되었습니다.',
        ))
    return findings


class HistoricalIngestionService:
    """Coordinate one-date-at-a-time archival, validation, and promotion."""

    def __init__(self, store, archive, source, service_key='', sleeper=None, max_attempts=3):
        self.store = store
        self.archive = archive
        self.source = source
        self.service_key = service_key
        self.sleeper = sleeper or time.sleep
        self.max_attempts = int(max_attempts)

    def create_job(self, start_date, end_date, source_mode='api'):
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        if start > end:
            raise ValueError('수집 시작일은 종료일보다 늦을 수 없습니다.')
        dates = [day.strftime('%Y-%m-%d') for day in pd.date_range(start, end) if day.weekday() < 5]
        if not dates:
            raise ValueError('요청 기간에 수집할 평일이 없습니다.')
        return self.store.create_job(start.date().isoformat(), end.date().isoformat(), dates, source_mode)

    def request_pause(self, job_id):
        self.store.set_pause(job_id, True)

    def resume(self, job_id):
        self.store.set_pause(job_id, False)

    def process_next(self, job_id):
        if self.store.pause_requested(job_id):
            return None
        day = self.store.next_processable_date(job_id, self.max_attempts)
        if day is None:
            return None
        self.store.transition_date(job_id, day, DateState.DOWNLOADING)
        try:
            result = self.source.fetch(day, self.service_key)
            return self._ingest_result(job_id, day, result)
        except Exception as exc:
            current = self.store.date_state(job_id, day)
            if current in {
                DateState.DOWNLOADING, DateState.DOWNLOADED,
                DateState.PARSED, DateState.VALIDATED,
            }:
                safe_message = str(exc)
                if self.service_key:
                    safe_message = safe_message.replace(self.service_key, '[redacted]')
                self.store.transition_date(job_id, day, DateState.FAILED, safe_message)
            raise

    def import_csv(self, job_id, day, content):
        if self.store.pause_requested(job_id):
            raise HistoricalMarketError('일시중지된 작업에는 CSV를 가져올 수 없습니다.')
        current = self.store.date_state(job_id, day)
        if current not in {
            DateState.PENDING, DateState.FAILED,
            DateState.QUARANTINED, DateState.CALENDAR_UNRESOLVED,
        }:
            raise HistoricalMarketError(f'{current.value} 상태에는 CSV를 가져올 수 없습니다.')
        self.store.transition_date(job_id, day, DateState.DOWNLOADING)
        try:
            from quantdesk.historical_market_source import OfficialCsvSource
            result = OfficialCsvSource().from_bytes(day, content)
            return self._ingest_result(job_id, day, result)
        except Exception as exc:
            current = self.store.date_state(job_id, day)
            if current in {DateState.DOWNLOADING, DateState.DOWNLOADED, DateState.PARSED}:
                self.store.transition_date(job_id, day, DateState.FAILED, str(exc))
            raise

    def _ingest_result(self, job_id, day, result):
        previous = self.store.raw_versions(day)
        archived = self.archive.store(result)
        raw_id = self.store.record_raw_version(archived, result.safe_metadata)
        self.store.transition_date(job_id, day, DateState.DOWNLOADED)

        if result.safe_metadata.get('non_session_candidate'):
            attempts = int(self.store.job_dates(job_id).set_index('trade_date').loc[day, 'attempts'])
            target = DateState.CALENDAR_UNRESOLVED if attempts >= self.max_attempts else DateState.FAILED
            self.store.transition_date(job_id, day, target, '공식 자료가 없어 거래일 여부를 확인해야 합니다.')
            return {'trade_date': day, 'state': target.value}

        from quantdesk.historical_market_source import decode_source_result
        source_frame = decode_source_result(result)
        normalized = normalize_daily_market(source_frame, day)
        self.store.transition_date(job_id, day, DateState.PARSED)
        changed = not previous.empty and archived.sha256 not in set(previous.sha256)
        findings = validate_daily_market(
            normalized,
            day,
            prior_summaries=self.store.prior_summaries(day),
            changed_checksum=changed,
        )
        self.store.save_findings(raw_id, findings)
        if any(item.severity == 'quarantine' for item in findings):
            self.store.transition_date(job_id, day, DateState.QUARANTINED, '검증 경고로 격리되었습니다.')
            return {'trade_date': day, 'state': DateState.QUARANTINED.value}
        self.store.transition_date(job_id, day, DateState.VALIDATED)
        self.store.promote_day(job_id, day, raw_id, normalized)
        return {'trade_date': day, 'state': DateState.PROMOTED.value}

    def run(self, job_id, on_step=None):
        from quantdesk.historical_market_source import (
            SourceAuthenticationError,
            SourceProviderError,
            SourceQuotaError,
            SourceRateLimitError,
            SourceTimeoutError,
        )
        results = []
        while not self.store.pause_requested(job_id):
            if on_step:
                on_step()
            try:
                result = self.process_next(job_id)
            except (SourceAuthenticationError, SourceQuotaError):
                raise
            except (SourceProviderError, SourceRateLimitError, SourceTimeoutError):
                dates = self.store.job_dates(job_id)
                failed = dates[dates.state == DateState.FAILED.value]
                if failed.empty:
                    raise
                attempts = int(failed.iloc[0].attempts)
                if attempts < self.max_attempts:
                    self.sleeper(attempts)
                    continue
                continue
            if result is None:
                break
            results.append(result)
            if result['state'] == DateState.FAILED.value:
                attempts = int(
                    self.store.job_dates(job_id).set_index('trade_date').loc[result['trade_date'], 'attempts']
                )
                self.sleeper(attempts)
        return results
