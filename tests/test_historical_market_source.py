import unittest

import requests

from quantdesk.historical_market import normalize_daily_market
from quantdesk.historical_market_source import (
    DataGoKrDailySource,
    OfficialCsvSource,
    SourceAuthenticationError,
    decode_source_result,
)


def api_item(code, market='KOSPI', date='20260925'):
    return {
        'basDt': date, 'srtnCd': code, 'itmsNm': '테스트' + code,
        'mrktCtg': market, 'mkp': '100', 'hipr': '110', 'lopr': '90',
        'clpr': '105', 'trqu': '10', 'trPrc': '1050',
        'lstgStCnt': '1000', 'mrktTotAmt': '105000',
    }


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, params, timeout, headers):
        self.calls.append((url, dict(params), timeout, dict(headers)))
        response = self.payloads.pop(0)
        return response if isinstance(response, FakeResponse) else FakeResponse(response)


def success_payload(items, total):
    return {
        'response': {
            'header': {'resultCode': '00', 'resultMsg': 'NORMAL SERVICE.'},
            'body': {'totalCount': total, 'items': {'item': items}},
        },
    }


class DataGoKrDailySourceTests(unittest.TestCase):
    def test_fetches_every_page_and_preserves_rows_without_leaking_key(self):
        session = FakeSession([
            success_payload([api_item('005930'), api_item('000660', 'KOSDAQ')], 3),
            success_payload([api_item('035420')], 3),
        ])
        source = DataGoKrDailySource(session=session, page_size=2, sleeper=lambda _: None)

        result = source.fetch('2026-09-25', 'very-secret-key')
        rows = decode_source_result(result)

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1]['basDt'], '20260925')
        self.assertEqual(session.calls[1][1]['pageNo'], 2)
        self.assertEqual(len(rows), 3)
        self.assertNotIn('very-secret-key', repr(result))
        self.assertNotIn('very-secret-key', str(result.safe_metadata))
        normalized = normalize_daily_market(rows, '2026-09-25')
        self.assertEqual(normalized.code.tolist(), ['005930', '000660', '035420'])

    def test_official_authentication_error_is_typed_and_secret_safe(self):
        session = FakeSession([{
            'response': {
                'header': {'resultCode': '20', 'resultMsg': 'SERVICE KEY IS NOT REGISTERED'},
                'body': {},
            },
        }])

        with self.assertRaises(SourceAuthenticationError) as caught:
            DataGoKrDailySource(session=session).fetch('2026-09-25', 'very-secret-key')

        self.assertNotIn('very-secret-key', str(caught.exception))

    def test_gateway_authentication_error_is_read_before_http_status_is_raised(self):
        session = FakeSession([FakeResponse({
            'OpenAPI_ServiceResponse': {
                'cmmMsgHeader': {
                    'errMsg': 'SERVICE_KEY_IS_NOT_REGISTERED_ERROR',
                    'returnReasonCode': '30',
                },
            },
        }, status_code=403)])

        with self.assertRaisesRegex(SourceAuthenticationError, '30'):
            DataGoKrDailySource(session=session).fetch('2026-09-25', 'very-secret-key')

    def test_encoded_service_key_is_decoded_once_before_requests_encodes_it(self):
        session = FakeSession([success_payload([], 0)])

        DataGoKrDailySource(session=session).fetch('2026-09-25', 'abc%2Bdef%2Fghi%3D')

        self.assertEqual(session.calls[0][1]['serviceKey'], 'abc+def/ghi=')

    def test_empty_success_is_marked_as_non_session_candidate(self):
        session = FakeSession([success_payload([], 0)])

        result = DataGoKrDailySource(session=session).fetch('2026-09-25', 'key')

        self.assertTrue(result.safe_metadata['non_session_candidate'])
        self.assertTrue(decode_source_result(result).empty)


class OfficialCsvSourceTests(unittest.TestCase):
    def test_csv_uses_same_source_contract_and_rejects_wrong_date(self):
        content = (
            '기준일자,종목코드,종목명,시장구분,시가,고가,저가,종가,거래량,거래대금,시가총액,상장주식수\n'
            '20260925,005930,삼성전자,KOSPI,100,110,90,105,10,1050,105000,1000\n'
            '20260925,000660,테스트,KOSDAQ,100,110,90,105,10,1050,105000,1000\n'
        ).encode('utf-8')
        result = OfficialCsvSource().from_bytes('2026-09-25', content)
        self.assertEqual(len(decode_source_result(result)), 2)

        with self.assertRaisesRegex(ValueError, '기준일'):
            OfficialCsvSource().from_bytes('2026-09-24', content)


if __name__ == '__main__':
    unittest.main()
