"""Official-source contracts and immutable archives for investability status."""

import gzip
import hashlib
import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pandas as pd


SUPPORTED_DATASETS = {'daily_status', 'management_history'}
MAX_RAW_BYTES = 50 * 1024 * 1024
KRX_STATUS_ENDPOINT = 'https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd'


class InvestabilitySourceError(ValueError):
    """Raised when official investability evidence cannot be trusted."""


@dataclass(frozen=True)
class InvestabilityRawArtifact:
    dataset: str
    requested_date: str
    sha256: str
    path: Path
    observed_at: str
    source_url: str


_COLUMN_ALIASES = {
    '기준일': 'requested_date', 'BAS_DD': 'requested_date', 'basDt': 'requested_date',
    '표준코드': 'standard_code', 'ISU_CD': 'standard_code',
    '단축코드': 'short_code', '종목코드': 'short_code', 'ISU_SRT_CD': 'short_code',
    '종목명': 'name', '한글 종목명': 'name', 'ISU_NM': 'name',
    '시장구분': 'market', 'MKT_NM': 'market',
    '주식종류': 'security_kind', 'KIND_STKCERT_TP_NM': 'security_kind',
    '관리종목여부': 'management_designation', 'MANG_ITEM_YN': 'management_designation',
    '거래정지여부': 'trading_suspension', 'TRD_STOP_YN': 'trading_suspension',
    '정리매매여부': 'liquidation_trading', 'CLSG_TRD_YN': 'liquidation_trading',
    '적용일': 'effective_date', 'EFFECTIVE_DD': 'effective_date',
    '공시일시': 'published_at', 'PUBLISHED_AT': 'published_at',
}

_CANONICAL_COLUMNS = [
    'dataset', 'requested_date', 'raw_version_id', 'source_row_number',
    'observed_at', 'standard_code', 'short_code', 'name', 'market',
    'security_kind', 'management_designation', 'trading_suspension',
    'liquidation_trading', 'effective_date', 'published_at',
]


def _iso_date(value, label):
    parsed = pd.to_datetime(value, errors='coerce')
    if pd.isna(parsed):
        raise InvestabilitySourceError(f'{label} 형식이 올바르지 않습니다.')
    return parsed.date().isoformat()


def _redacted_url(value):
    parts = urlsplit(str(value or ''))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def archive_investability_payload(root: Path, dataset: str, requested_date: str,
                                  payload: bytes, observed_at: str,
                                  source_url: str) -> InvestabilityRawArtifact:
    if dataset not in SUPPORTED_DATASETS:
        raise InvestabilitySourceError('지원하지 않는 투자 가능성 자료 종류입니다.')
    day = _iso_date(requested_date, '기준일')
    content = bytes(payload)
    if not content:
        raise InvestabilitySourceError('공식 원본이 비어 있습니다.')
    if len(content) > MAX_RAW_BYTES:
        raise InvestabilitySourceError('공식 원본 크기가 허용 한도를 초과했습니다.')
    root = Path(root).resolve()
    folder = (root / dataset / day).resolve()
    if not folder.is_relative_to(root):
        raise InvestabilitySourceError('원본 저장 경로가 허용 범위를 벗어났습니다.')
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    existing = list(folder.glob(f'*-{digest}.{dataset}.csv.gz'))
    if existing:
        target = existing[0]
        return InvestabilityRawArtifact(
            dataset, day, digest, target, observed_at, _redacted_url(source_url),
        )
    stamp = pd.Timestamp(observed_at).tz_convert('UTC').strftime('%Y%m%dT%H%M%SZ')
    target = (folder / f'{stamp}-{digest}.{dataset}.csv.gz').resolve()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='wb', dir=folder, prefix='.download-', suffix='.part', delete=False,
        ) as output:
            temporary = Path(output.name)
            with gzip.GzipFile(fileobj=output, mode='wb', mtime=0) as compressed:
                compressed.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return InvestabilityRawArtifact(
        dataset, day, digest, target, observed_at, _redacted_url(source_url),
    )


def decode_investability_csv(payload: bytes) -> pd.DataFrame:
    content = bytes(payload)
    if not content or content.lstrip().lower().startswith((b'<html', b'<!doctype')):
        raise InvestabilitySourceError('공식 원본이 비어 있거나 HTML 오류 응답입니다.')
    for encoding in ('utf-8-sig', 'utf-8', 'cp949'):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise InvestabilitySourceError('공식 CSV 문자 인코딩을 읽을 수 없습니다.')
    try:
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except (ValueError, pd.errors.ParserError) as exc:
        raise InvestabilitySourceError('공식 CSV 형식을 읽을 수 없습니다.') from exc
    if frame.empty or len(frame.columns) < 2:
        raise InvestabilitySourceError('공식 원본에 검증 가능한 행이 없습니다.')
    return frame


def _boolean(value, label):
    text = str(value or '').strip().upper()
    if text in {'Y', 'YES', '1', 'TRUE', '해당', '예'}:
        return True
    if text in {'N', 'NO', '0', 'FALSE', '미해당', '아니오'}:
        return False
    raise InvestabilitySourceError(f'{label} 값이 올바르지 않습니다.')


def _security_kind(value):
    text = str(value or '').strip().lower()
    if text in {'보통주', 'common', 'common stock'}:
        return 'common'
    if text in {'우선주', 'preferred', 'preferred stock'}:
        return 'preferred'
    if '스팩' in text or text == 'spac':
        return 'spac'
    if '리츠' in text or text == 'reit':
        return 'reit'
    if text == 'etf':
        return 'etf'
    if text == 'etn':
        return 'etn'
    return 'unknown'


def normalize_investability_rows(frame: pd.DataFrame, dataset: str,
                                 requested_date: str, raw_version_id: int,
                                 observed_at: str) -> pd.DataFrame:
    if dataset != 'daily_status':
        raise InvestabilitySourceError('지원하지 않는 투자 가능성 자료 종류입니다.')
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise InvestabilitySourceError('공식 원본에 검증 가능한 행이 없습니다.')
    day = _iso_date(requested_date, '기준일')
    data = frame.rename(columns=_COLUMN_ALIASES).copy()
    required = {
        'requested_date', 'short_code', 'name', 'market', 'security_kind',
        'management_designation', 'trading_suspension', 'liquidation_trading',
    }
    missing = required - set(data.columns)
    if missing:
        raise InvestabilitySourceError(
            f'공식 상태 필수 열이 없습니다: {", ".join(sorted(missing))}'
        )
    parsed_dates = data.requested_date.map(lambda value: _iso_date(value, '기준일'))
    if not parsed_dates.eq(day).all():
        raise InvestabilitySourceError('공식 원본 기준일이 요청 기준일과 다릅니다.')
    if 'standard_code' not in data:
        data['standard_code'] = None
    if 'effective_date' not in data:
        data['effective_date'] = day
    if 'published_at' not in data:
        data['published_at'] = None
    data['dataset'] = dataset
    data['requested_date'] = day
    data['raw_version_id'] = int(raw_version_id)
    data['source_row_number'] = range(1, len(data) + 1)
    data['observed_at'] = pd.Timestamp(observed_at).isoformat()
    data['standard_code'] = data.standard_code.fillna('').astype(str).str.strip().replace('', None)
    data['short_code'] = data.short_code.fillna('').astype(str).str.strip().str.replace(r'\.0$', '', regex=True).str.zfill(6)
    data['name'] = data.name.fillna('').astype(str).str.strip()
    data['market'] = data.market.fillna('').astype(str).str.strip().str.upper()
    data.loc[data.market.str.startswith('KOSPI'), 'market'] = 'KOSPI'
    data.loc[data.market.str.startswith('KOSDAQ'), 'market'] = 'KOSDAQ'
    data['security_kind'] = data.security_kind.map(_security_kind)
    for column, label in (
        ('management_designation', '관리종목 여부'),
        ('trading_suspension', '거래정지 여부'),
        ('liquidation_trading', '정리매매 여부'),
    ):
        data[column] = data[column].map(lambda value: _boolean(value, label))
    data['effective_date'] = data.effective_date.map(
        lambda value: _iso_date(value or day, '적용일')
    )
    data['published_at'] = data.published_at.replace('', None)
    if data.short_code.eq('').any() or data.name.eq('').any():
        raise InvestabilitySourceError('종목코드 또는 종목명이 비어 있습니다.')
    if not data.market.isin({'KOSPI', 'KOSDAQ'}).all():
        raise InvestabilitySourceError('시장구분 값이 올바르지 않습니다.')
    return data[_CANONICAL_COLUMNS].reset_index(drop=True)


def fetch_investability_snapshot(requested_date: str, http_get,
                                 service_key: str | None = None) -> bytes:
    day = _iso_date(requested_date, '기준일')
    params = {'date': day.replace('-', ''), 'locale': 'ko_KR'}
    if service_key:
        params['serviceKey'] = service_key
    try:
        response = http_get(
            KRX_STATUS_ENDPOINT, params=params,
            headers={'User-Agent': 'QuantDeskKR/0.1 personal-research'}, timeout=30,
        )
        if response.status_code in {400, 401, 403}:
            raise InvestabilitySourceError(
                'KRX 자동 조회 인증 또는 로그인에 실패했습니다. 공식 CSV를 내려받아 업로드하세요.'
            )
        response.raise_for_status()
    except InvestabilitySourceError:
        raise
    except Exception as exc:
        raise InvestabilitySourceError(
            'KRX 공식 상태 자료를 내려받을 수 없습니다. 공식 CSV를 확인하세요.'
        ) from exc
    return bytes(response.content)
