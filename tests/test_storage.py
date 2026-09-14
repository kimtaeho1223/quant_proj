import tempfile
import unittest
from pathlib import Path
from quantdesk.storage import Store
from quantdesk.data import sample_stocks


class StorageTests(unittest.TestCase):
    def test_round_trip_and_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'test.db')
            self.assertEqual(store.load_account(), ({}, 20_000_000))
            store.save_account({'000001': 5}, 1000)
            self.assertEqual(Store(Path(folder) / 'test.db').load_account(), ({'000001': 5}, 1000))
            store.save_snapshot({'orders': [], 'cash_after': 1000}, 'weekly', {'momentum': 45})
            self.assertEqual(store.history()[0]['note'], 'weekly')
            self.assertEqual(store.load_account(), ({'000001': 5}, 1000))

    def test_sample_is_deterministic(self):
        self.assertTrue(sample_stocks().equals(sample_stocks()))
        self.assertEqual(len(sample_stocks()), 80)
