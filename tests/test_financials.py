import unittest
import pandas as pd
from quantdesk.financials import extract_metrics, match_company, summarize_statement


def rows():
    return [dict(account_id='ifrs-full_ProfitLoss', sj_div='IS', thstrm_amount='1,000', currency='KRW'),
            dict(account_id='ifrs-full_Equity', sj_div='BS', thstrm_amount='10,000', frmtrm_amount='6,000', currency='KRW'),
            dict(account_id='ifrs-full_ProfitLoss', sj_div='CF', thstrm_amount='999', currency='KRW')]


class FinancialTests(unittest.TestCase):
    def test_uses_income_statement_and_average_equity(self):
        result = extract_metrics(rows(), 'OFS')
        self.assertEqual(result['roe'], 12.5)
        self.assertEqual(result['profit'], 1000)

    def test_ambiguous_income_blocks(self):
        with self.assertRaises(ValueError):
            extract_metrics(rows() + [dict(rows()[0], sj_div='CIS', thstrm_amount='2000')], 'OFS')

    def test_parent_accounts_required_for_consolidated(self):
        with self.assertRaises(ValueError):
            extract_metrics(rows(), 'CFS')

    def test_nonpositive_equity_blocks(self):
        data = rows()
        data[1]['frmtrm_amount'] = '-1'
        with self.assertRaises(ValueError):
            extract_metrics(data, 'OFS')

    def test_missing_listing_does_not_match_group_company(self):
        listing = pd.DataFrame([dict(Code='055550', Name='신한지주', Marcap=100000)])
        self.assertNotEqual(match_company('000010', {'000010': {'name':'신한은행'}}, listing), '')

    def test_exact_stock_code_allows_official_name_variants(self):
        listing = pd.DataFrame([dict(Code='005380', Name='현대차', Marcap=100000)])
        companies = {'005380': {'corp_code': '00164742', 'name': '현대자동차'}}
        self.assertEqual(match_company('005380', companies, listing), '')

    def test_value_ratio_only_when_company_matches(self):
        entry = dict(stock='000010', year=2025, basis='OFS', filed_at='2026-03-01', rows=rows())
        result = summarize_statement(entry, {'000010':{'name':'신한은행'}}, pd.DataFrame())
        self.assertEqual(result['ROE (%)'], 12.5)
        self.assertIsNone(result['연간 이익 / 시가총액 (%)'])
