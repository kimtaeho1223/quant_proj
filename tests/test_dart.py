import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import requests
from quantdesk.dart import DartClient, DartStore, DartError, validate_statement


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
