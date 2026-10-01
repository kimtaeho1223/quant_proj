"""Official sources for daily full-market Korean stock data."""

import io
import json
import time
from urllib.parse import unquote

import pandas as pd
import requests

from quantdesk.historical_market import DailySourceResult, DailyValidationError, normalize_daily_market


class HistoricalSourceError(ValueError):
    pass


class SourceAuthenticationError(HistoricalSourceError):
    pass


class SourceQuotaError(HistoricalSourceError):
    pass


class SourceRateLimitError(HistoricalSourceError):
    pass


class SourceTimeoutError(HistoricalSourceError):
    pass


class SourceProviderError(HistoricalSourceError):
    pass


class DataGoKrDailySource:
    ENDPOINT = (
        'https://apis.data.go.kr/1160100/service/'
        'GetStockSecuritiesInfoService/getStockPriceInfo'
    )

    def __init__(self, session=None, page_size=1000, timeout=(5, 30), sleeper=None, interval=0.5):
        self.session = session or requests.Session()
        self.page_size = int(page_size)
        self.timeout = timeout
        self.sleeper = sleeper or time.sleep
        self.interval = float(interval)

    def fetch(self, requested_date, service_key):
        if not service_key:
            raise SourceAuthenticationError('공공데이터포털 인증키가 필요합니다.')
        service_key = unquote(str(service_key).strip())
        pages = []
        total = None
        page = 1
        received = 0
        while total is None or received < total:
            params = {
                'serviceKey': service_key,
                'resultType': 'json',
                'numOfRows': self.page_size,
                'pageNo': page,
                'basDt': requested_date.replace('-', ''),
            }
            try:
                response = self.session.get(
                    self.ENDPOINT,
                    params=params,
                    timeout=self.timeout,
                    headers={'User-Agent': 'QuantDeskKR/0.1 personal-research'},
                )
            except requests.Timeout as exc:
                raise SourceTimeoutError('공식 제공처 응답 시간이 초과되었습니다.') from exc
            except requests.RequestException as exc:
                raise SourceProviderError('공식 제공처 응답을 읽을 수 없습니다.') from exc
            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceProviderError('공식 제공처 응답을 읽을 수 없습니다.') from exc
            gateway_header = (
                payload.get('OpenAPI_ServiceResponse', {}).get('cmmMsgHeader', {})
                if isinstance(payload, dict) else {}
            )
            if gateway_header:
                self._raise_provider_error(
                    gateway_header.get('returnReasonCode'),
                    gateway_header.get('errMsg') or gateway_header.get('returnAuthMsg'),
                )
            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                raise SourceProviderError(
                    f'공식 제공처 HTTP 오류 ({getattr(response, "status_code", "unknown")})'
                ) from exc
            body = self._validated_body(payload)
            pages.append(payload)
            items = _items(body)
            received += len(items)
            total = int(body.get('totalCount') or 0)
            if total == 0 or not items:
                break
            page += 1
            if received < total:
                self.sleeper(self.interval)

        content = json.dumps(
            {'pages': pages}, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        ).encode('utf-8')
        return DailySourceResult(
            requested_date=requested_date,
            source_type='data_go_kr',
            content=content,
            metadata={
                'endpoint': self.ENDPOINT,
                'page_count': len(pages),
                'total_count': total or 0,
                'non_session_candidate': (total or 0) == 0,
            },
        )

    @staticmethod
    def _validated_body(payload):
        try:
            response = payload['response']
            header = response['header']
            code = str(header.get('resultCode', ''))
        except (KeyError, TypeError) as exc:
            raise SourceProviderError('공식 제공처 응답 구조가 올바르지 않습니다.') from exc
        if code == '00':
            return response.get('body') or {}
        message = str(header.get('resultMsg') or '공식 제공처 오류')
        DataGoKrDailySource._raise_provider_error(code, message)

    @staticmethod
    def _raise_provider_error(code, message):
        code = str(code or '')
        message = str(message or '공식 제공처 오류')
        if code in {'20', '30', '31'}:
            raise SourceAuthenticationError(f'공공데이터포털 인증 오류 ({code}): {message}')
        if code == '22':
            raise SourceQuotaError(f'공공데이터포털 일일 호출량 오류 ({code}): {message}')
        if code == '23':
            raise SourceRateLimitError(f'공공데이터포털 호출 속도 오류 ({code}): {message}')
        if code == '05':
            raise SourceTimeoutError(f'공식 제공처 연결 시간 초과 ({code}): {message}')
        raise SourceProviderError(f'공식 제공처 오류 ({code}): {message}')


class OfficialCsvSource:
    def from_bytes(self, requested_date, content):
        result = DailySourceResult(
            requested_date=requested_date,
            source_type='official_csv',
            content=bytes(content),
            metadata={'upload_size': len(content)},
        )
        frame = decode_source_result(result)
        try:
            normalize_daily_market(frame, requested_date)
        except DailyValidationError as exc:
            raise ValueError(f'공식 CSV 기준일 또는 형식을 확인해 주세요: {exc}') from exc
        return result


def _items(body):
    items = (body.get('items') or {}).get('item') or []
    if isinstance(items, dict):
        return [items]
    return list(items)


def decode_source_result(result):
    if result.source_type == 'data_go_kr':
        try:
            envelope = json.loads(result.content.decode('utf-8'))
            rows = []
            for payload in envelope['pages']:
                rows.extend(_items(payload['response'].get('body') or {}))
            return pd.DataFrame(rows)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourceProviderError('보관된 공식 API 원본을 해석할 수 없습니다.') from exc
    if result.source_type == 'official_csv':
        for encoding in ('utf-8-sig', 'cp949'):
            try:
                return pd.read_csv(io.BytesIO(result.content), encoding=encoding, dtype=str)
            except UnicodeDecodeError:
                continue
            except pd.errors.ParserError as exc:
                raise SourceProviderError('공식 CSV를 해석할 수 없습니다.') from exc
        raise SourceProviderError('공식 CSV 문자 인코딩을 확인해 주세요.')
    raise SourceProviderError(f'지원하지 않는 원본 출처입니다: {result.source_type}')
