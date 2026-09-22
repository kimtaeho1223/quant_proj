import unittest
import pandas as pd
from quantdesk.real_ranking_ui import account_editor_rows


class RealRankingUiTests(unittest.TestCase):
    def test_account_editor_keeps_holdings_outside_current_candidates(self):
        candidates = pd.DataFrame([dict(Code='000001', Name='후보')])
        listing = pd.DataFrame([
            dict(Code='000001', Name='후보'),
            dict(Code='000002', Name='기존보유'),
        ])

        rows = account_editor_rows(candidates, listing, {'000002': 3}, {'000001': 1000, '000002': 2000})

        self.assertEqual(rows.code.tolist(), ['000001', '000002'])
        self.assertEqual(rows.quantity.tolist(), [0, 3])
        self.assertEqual(rows.price.tolist(), [1000, 2000])


if __name__ == '__main__':
    unittest.main()
