import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import pandas as pd
import requests
from quantdesk.dart import DartClient, DartStore, DartError, DartNoDataError, refresh_top_statements, validate_statement


class DartTests(unittest.TestCase):
    def test_network_error_does_not_expose_key(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError('secret-key-in-url')
        with self.assertRaises(DartError) as caught:
            DartClient('a' * 40, session).request('list.json', {})
        self.assertNotIn('secret-key', str(caught.exception))

    def test_invalid_key_blocks_requests(self):
        with self.assertRaises(DartError):
            DartClient('bad')

    def test_no_data_status_has_a_distinct_error_for_basis_fallback(self):
        response = Mock(status_code=200, content=b'')
        response.raise_for_status.return_value = None
        response.json.return_value = {'status': '013', 'message': '조회된 데이터가 없습니다.'}
        session = Mock()
        session.get.return_value = response

        with self.assertRaises(DartNoDataError):
            DartClient('a' * 40, session).request('fnlttSinglAcntAll.json', {})

    def test_statement_requires_matching_receipt(self):
        rows = [dict(rcept_no='20260316000001', account_nm='자본총계')]
        with self.assertRaises(DartError):
            validate_statement(rows, [])
        result = validate_statement(rows, [dict(rcept_no='20260316000001', rcept_dt='20260316', report_nm='사업보고서')])
        self.assertEqual(result['filed_at'], '2026-03-16')

    def test_roundtrip_keeps_revisions(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DartStore(Path(folder) / 'dart.db')
            for receipt in ['20260316000001', '20260401000001']:
                store.save_statement('005930', 2025, 'CFS', dict(receipt=receipt, filed_at='2026-03-16', rows=[{'account_nm':'자본총계'}]))
            self.assertEqual(len(store.statements()), 2)

    def test_top_statement_batch_prefers_cfs_and_saves_result(self):
        listing = pd.DataFrame([
            dict(Code='000001', Name='알파', Market='KOSPI', Marcap=300, Amount=30),
        ])
        companies = {'000001': {'corp_code': '00000001', 'name': '알파'}}

        class Client:
            def annual(self, corp, year, basis):
                return dict(receipt='20260316000001', filed_at='2026-03-16',
                            rows=[{'corp_code': corp, 'rcept_no': '20260316000001'}])

        with tempfile.TemporaryDirectory() as folder:
            store = DartStore(Path(folder) / 'dart.db')
            results = refresh_top_statements(store, listing, companies, 2025, Client())

            self.assertEqual(results, [dict(stock='000001', name='알파', status='성공', basis='CFS',
                                                   message='연결 재무제표 저장')])
            self.assertEqual(store.statements()[0]['basis'], 'CFS')
            self.assertEqual(store.latest_results()[0]['상태'], '성공')

    def test_top_statement_batch_falls_back_to_ofs_only_for_no_data(self):
        listing = pd.DataFrame([
            dict(Code='000001', Name='알파', Market='KOSPI', Marcap=300, Amount=30),
        ])
        companies = {'000001': {'corp_code': '00000001', 'name': '알파'}}
        calls = []

        class Client:
            def annual(self, corp, year, basis):
                calls.append(basis)
                if basis == 'CFS':
                    raise DartNoDataError('조회된 자료가 없습니다.')
                return dict(receipt='20260316000002', filed_at='2026-03-16', rows=[])

        with tempfile.TemporaryDirectory() as folder:
            store = DartStore(Path(folder) / 'dart.db')
            results = refresh_top_statements(store, listing, companies, 2025, Client())

            self.assertEqual(calls, ['CFS', 'OFS'])
            self.assertEqual(results[0]['basis'], 'OFS')
            self.assertEqual(store.statements()[0]['basis'], 'OFS')

    def test_top_statement_batch_does_not_fallback_on_auth_error(self):
        listing = pd.DataFrame([
            dict(Code='000001', Name='알파', Market='KOSPI', Marcap=300, Amount=30),
        ])
        companies = {'000001': {'corp_code': '00000001', 'name': '알파'}}
        calls = []

        class Client:
            def annual(self, corp, year, basis):
                calls.append(basis)
                raise DartError('요청 한도를 초과했습니다.')

        with tempfile.TemporaryDirectory() as folder:
            store = DartStore(Path(folder) / 'dart.db')
            results = refresh_top_statements(store, listing, companies, 2025, Client())

            self.assertEqual(calls, ['CFS'])
            self.assertEqual(results[0]['status'], '실패')
            self.assertEqual(results[0]['message'], '요청 한도를 초과했습니다.')

    def test_top_statement_batch_continues_after_company_mismatch(self):
        listing = pd.DataFrame([
            dict(Code='000001', Name='알파', Market='KOSPI', Marcap=300, Amount=30),
            dict(Code='000002', Name='베타', Market='KOSDAQ', Marcap=200, Amount=20),
        ])
        companies = {
            '000001': {'corp_code': '00000001', 'name': '다른알파'},
            '000002': {'corp_code': '00000002', 'name': '베타'},
        }

        class Client:
            def annual(self, corp, year, basis):
                return dict(receipt='20260316000002', filed_at='2026-03-16', rows=[])

        with tempfile.TemporaryDirectory() as folder:
            store = DartStore(Path(folder) / 'dart.db')
            results = refresh_top_statements(store, listing, companies, 2025, Client())

            self.assertEqual([result['status'] for result in results], ['실패', '성공'])
            self.assertEqual([entry['stock'] for entry in store.statements()], ['000002'])
