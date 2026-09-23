import unittest
import pandas as pd
from quantdesk.real_ranking import candidate_universe, price_factors, select_statement, build_ranking


def listing():
    return pd.DataFrame([
        dict(Code='000001', Name='알파', Market='KOSPI', Marcap=500_000_000_000, Amount=2_000_000_000),
        dict(Code='000002', Name='알파우', Market='KOSPI', Marcap=900_000_000_000, Amount=2_000_000_000),
        dict(Code='000003', Name='베타', Market='KOSDAQ', Marcap=400_000_000_000, Amount=2_000_000_000),
        dict(Code='000004', Name='감마스팩', Market='KOSDAQ', Marcap=800_000_000_000, Amount=2_000_000_000),
        dict(Code='000005', Name='델타', Market='KONEX', Marcap=700_000_000_000, Amount=2_000_000_000),
    ])


def prices(days=252):
    dates = pd.bdate_range('2025-01-01', periods=days)
    return pd.DataFrame({'Date': dates.strftime('%Y-%m-%d'), 'Close': [100 + i for i in range(days)]})


def statement(stock='000001', basis='CFS', filed_at='2025-12-31'):
    parent = basis == 'CFS'
    profit = 'ProfitLossAttributableToOwnersOfParent' if parent else 'ProfitLoss'
    equity = 'EquityAttributableToOwnersOfParent' if parent else 'Equity'
    return dict(stock=stock, year=2024, basis=basis, receipt='20250301000001', filed_at=filed_at,
        rows=[dict(account_id='ifrs-full_' + profit, sj_div='IS', thstrm_amount='1000', currency='KRW'),
              dict(account_id='ifrs-full_' + equity, sj_div='BS', thstrm_amount='10000', frmtrm_amount='9000', currency='KRW')])


class RealRankingTests(unittest.TestCase):
    def test_candidate_universe_excludes_non_common_and_limits_100(self):
        universe = candidate_universe(listing(), limit=100)
        self.assertEqual(universe.Code.tolist(), ['000001', '000003'])

    def test_price_factors_uses_252_observations_before_date(self):
        result = price_factors(prices(), '2025-12-31')
        self.assertAlmostEqual(result['momentum'], 251.0)
        self.assertGreater(result['volatility'], 0)
        with self.assertRaises(ValueError):
            price_factors(prices(251), '2025-12-31')

    def test_statement_is_filed_and_prefers_consolidated(self):
        records = [statement(basis='OFS'), statement(basis='CFS'), statement(filed_at='2026-01-01')]
        selected = select_statement(records, '000001', '2025-12-31')
        self.assertEqual(selected['basis'], 'CFS')
        self.assertIsNone(select_statement(records, '000001', '2024-01-01'))

    def test_corrected_filing_changes_only_after_correction_date(self):
        original = statement(filed_at='2025-03-01')
        corrected = statement(filed_at='2025-05-01')
        corrected['receipt'] = '20250501000002'

        before = select_statement([original, corrected], '000001', '2025-04-10')
        after = select_statement([original, corrected], '000001', '2025-05-10')

        self.assertEqual(before['receipt'], '20250301000001')
        self.assertEqual(after['receipt'], '20250501000002')

    def test_build_ranking_explains_missing_financials(self):
        table, excluded = build_ranking(listing(), {'000001':prices(), '000003':prices()}, [statement()],
            {'000001':{'corp_code':'00000001', 'name':'알파'}}, '2025-12-31')
        self.assertEqual(table.code.tolist(), ['000001'])
        self.assertIn('공시일이 지난 재무제표 없음', excluded.loc[excluded.Code == '000003', '제외 사유'].iloc[0])


if __name__ == '__main__':
    unittest.main()
