"""Official-source contracts and immutable archives for security lifecycles."""

import gzip
import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import requests


SUPPORTED_DATASETS = {
    'security_master', 'new_listings', 'delistings', 'identifier_changes',
}


class LifecycleSourceError(ValueError):
    """Raised when official lifecycle evidence cannot be trusted."""


def _safe_metadata(metadata):
    secret_names = {'servicekey', 'service_key', 'authorization', 'api_key', 'apikey'}
    safe = {}
    for key, value in dict(metadata or {}).items():
        name = str(key)
        if name.lower() in secret_names:
            continue
        if isinstance(value, str) and name.lower() in {'url', 'source_url', 'request_url'}:
            parts = urlsplit(value)
            value = urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))
        safe[name] = value
    return safe


@dataclass(frozen=True)
class LifecycleSourceResult:
    dataset: str
    scope_start: str | None
    scope_end: str | None
    content: bytes = field(repr=False)
    metadata: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if self.dataset not in SUPPORTED_DATASETS:
            raise LifecycleSourceError('지원하지 않는 생애주기 자료 종류입니다.')
        object.__setattr__(self, 'metadata', MappingProxyType(_safe_metadata(self.metadata)))

    @property
    def safe_metadata(self):
        return dict(self.metadata)


@dataclass(frozen=True)
class LifecycleArchivedRaw:
    dataset: str
    scope_start: str | None
    scope_end: str | None
    sha256: str
    path: Path
    fetched_at: str


class LifecycleRawArchive:
    def __init__(self, root, clock=None, replace_func=None):
        self.root = Path(root).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.replace_func = replace_func or os.replace

    def store(self, result):
        if result.dataset not in SUPPORTED_DATASETS:
            raise LifecycleSourceError('지원하지 않는 생애주기 자료 종류입니다.')
        digest = hashlib.sha256(result.content).hexdigest()
        scope = f'{result.scope_start or "all"}_{result.scope_end or "all"}'
        if not re.fullmatch(r'[0-9-]+_[0-9-]+|all_all|all_[0-9-]+|[0-9-]+_all', scope):
            raise LifecycleSourceError('원본 조회 범위 형식이 올바르지 않습니다.')
        folder = (self.root / result.dataset / scope).resolve()
        if not folder.is_relative_to(self.root):
            raise LifecycleSourceError('원본 저장 경로가 허용 범위를 벗어났습니다.')
        folder.mkdir(parents=True, exist_ok=True)
        matches = list(folder.glob(f'*-{digest}.{result.dataset}.gz'))
        if matches:
            target = matches[0]
            return LifecycleArchivedRaw(
                result.dataset, result.scope_start, result.scope_end,
                digest, target, target.name.split('-', 1)[0],
            )

        fetched = self.clock().astimezone(timezone.utc)
        stamp = fetched.strftime('%Y%m%dT%H%M%SZ')
        target = (folder / f'{stamp}-{digest}.{result.dataset}.gz').resolve()
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
        return LifecycleArchivedRaw(
            result.dataset, result.scope_start, result.scope_end,
            digest, target, fetched.isoformat(),
        )


def decode_lifecycle_source(result):
    content = bytes(result.content)
    if not content or content.lstrip().lower().startswith((b'<html', b'<!doctype')):
        raise LifecycleSourceError('공식 원본이 비어 있거나 HTML 오류 응답입니다.')
    decoded = None
    for encoding in ('utf-8-sig', 'utf-8', 'cp949'):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise LifecycleSourceError('공식 CSV 문자 인코딩을 읽을 수 없습니다.')
    try:
        frame = pd.read_csv(io.StringIO(decoded), dtype=str, keep_default_na=False)
    except (ValueError, pd.errors.ParserError) as exc:
        raise LifecycleSourceError('공식 CSV 형식을 읽을 수 없습니다.') from exc
    if frame.empty or len(frame.columns) < 2:
        raise LifecycleSourceError('공식 원본에 검증 가능한 행이 없습니다.')
    return frame


class OfficialLifecycleCsvSource:
    def from_bytes(self, dataset, content, scope_start=None, scope_end=None):
        return LifecycleSourceResult(
            dataset, scope_start, scope_end, bytes(content),
            {'provider': 'KRX', 'source_type': 'official_csv'},
        )


_DATASET_QUERIES = {
    'security_master': ('dbms/MDC/STAT/standard/MDCSTAT01901', 'MDCSTAT019'),
    'new_listings': ('dbms/MDC/STAT/issue/MDCSTAT20001', 'MDCSTAT200'),
    'delistings': ('dbms/MDC/STAT/issue/MDCSTAT23801', 'MDCSTAT238'),
    # Historical identifier evidence may require official CSV fallback. The
    # current master endpoint still supplies official current identifiers.
    'identifier_changes': ('dbms/MDC/STAT/standard/MDCSTAT01901', 'MDCSTAT019'),
}


class KrxLifecycleSource:
    endpoint = 'https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd'

    def __init__(self, session=None, timeout=30):
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch(self, dataset, scope_start=None, scope_end=None):
        if dataset not in _DATASET_QUERIES:
            raise LifecycleSourceError('지원하지 않는 KRX 생애주기 자료입니다.')
        bld, screen = _DATASET_QUERIES[dataset]
        data = {
            'bld': bld, 'locale': 'ko_KR', 'mktId': 'ALL', 'segTpCd': 'ALL',
            'share': '1',
            'strtDd': str(scope_start or '').replace('-', ''),
            'endDd': str(scope_end or '').replace('-', ''),
        }
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Referer': f'https://data.krx.co.kr/contents/MDC/STAT/{screen}.jsp',
        }
        try:
            response = self.session.post(
                self.endpoint, data=data, headers=headers, timeout=self.timeout,
            )
            if response.status_code == 400 and response.content.strip() == b'LOGOUT':
                raise LifecycleSourceError(
                    'KRX 자동 조회에 로그인 세션이 필요합니다. 공식 CSV를 내려받아 업로드하세요.'
                )
            response.raise_for_status()
        except LifecycleSourceError:
            raise
        except Exception as exc:
            raise LifecycleSourceError('KRX 공식 자료를 내려받을 수 없습니다.') from exc
        content = bytes(response.content)
        if content.lstrip().startswith(b'{'):
            try:
                payload = json.loads(content.decode('utf-8'))
                rows = next(value for value in payload.values() if isinstance(value, list) and value)
                content = pd.DataFrame(rows).to_csv(index=False).encode('utf-8-sig')
            except (ValueError, StopIteration, UnicodeDecodeError) as exc:
                raise LifecycleSourceError('KRX 공식 응답에 자료 행이 없습니다.') from exc
        result = LifecycleSourceResult(
            dataset, scope_start, scope_end, content,
            {
                'provider': 'KRX', 'official_screen': screen,
                'source_url': self.endpoint,
            },
        )
        decode_lifecycle_source(result)
        return result
