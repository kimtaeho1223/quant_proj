import unittest

import pandas as pd

from quantdesk.backtest_execution import (
    BacktestExecutionError,
    default_cost_model,
    execute_rebalance,
    value_portfolio,
)


def ranked_fixture(count=20, signal_close=9_000):
    return pd.DataFrame([
        {
            'rank': index,
            'code': f'{index:06d}',
            'name': f'기업{index}',
            'price': signal_close,
        }
        for index in range(1, count + 1)
    ])


class BacktestExecutionTests(unittest.TestCase):
    def test_execution_uses_open_prices_and_never_signal_closes(self):
        ranked = ranked_fixture(signal_close=9_000)
        opens = {code: 10_000 for code in ranked.code}

        result = execute_rebalance(
            ranked, {}, 10_000_000, opens, default_cost_model(),
        )

        self.assertEqual(result['orders'][0]['price'], 10_000)
        self.assertTrue(all(order['price'] != 9_000 for order in result['orders']))

    def test_expensive_target_is_unfilled_without_negative_cash(self):
        ranked = ranked_fixture(count=1)

        result = execute_rebalance(
            ranked, {}, 10_000_000, {'000001': 600_000}, default_cost_model(),
        )

        self.assertGreaterEqual(result['cash_after'], 0)
        self.assertEqual(result['unfilled'][0]['reason'], '목표 배정액으로 1주 매수 불가')

    def test_sells_fund_buys_and_cash_reconciles(self):
        ranked = ranked_fixture()
        opens = {code: 10_000 for code in ranked.code}
        opens['999999'] = 20_000

        result = execute_rebalance(
            ranked, {'999999': 100}, 8_000_000, opens,
            {'fee_rate': 0.001, 'tax_rate': 0.002, 'slippage_rate': 0.0},
        )

        buys = sum(order['gross_amount'] for order in result['orders'] if order['side'] == '매수')
        sells = sum(order['gross_amount'] for order in result['orders'] if order['side'] == '매도')
        costs = sum(order['cost_total'] for order in result['orders'])
        self.assertAlmostEqual(result['cash_after'], 8_000_000 + sells - buys - costs)
        self.assertGreaterEqual(result['cash_after'], 0)

    def test_order_below_minimum_is_logged_unfilled(self):
        ranked = ranked_fixture(count=1)

        result = execute_rebalance(
            ranked, {}, 1_000_000, {'000001': 10_000}, default_cost_model(),
        )

        self.assertEqual(result['orders'], [])
        self.assertEqual(result['unfilled'][0]['reason'], '최소 주문금액 미만')

    def test_cost_breakdown_separates_fee_tax_and_slippage(self):
        ranked = ranked_fixture()
        opens = {code: 10_000 for code in ranked.code}

        result = execute_rebalance(
            ranked, {}, 10_000_000, opens,
            {'fee_rate': 0.001, 'tax_rate': 0.002, 'slippage_rate': 0.003},
        )

        first = result['orders'][0]
        self.assertGreater(first['fee'], 0)
        self.assertEqual(first['tax'], 0)
        self.assertGreater(first['slippage'], 0)
        self.assertAlmostEqual(first['cost_total'], first['fee'] + first['tax'] + first['slippage'])

    def test_suspended_holding_uses_last_close_for_valuation_only(self):
        result = value_portfolio(
            {'000001': 10}, 100_000, {}, {'000001': 9_000},
        )

        self.assertEqual(result['equity'], 190_000)
        self.assertEqual(result['price_sources']['000001'], 'carried')

    def test_missing_target_open_is_not_replaced_with_signal_close(self):
        result = execute_rebalance(
            ranked_fixture(count=1, signal_close=9_000), {}, 10_000_000,
            {}, default_cost_model(),
        )

        self.assertEqual(result['orders'], [])
        self.assertEqual(result['unfilled'][0]['reason'], '다음 거래일 시가 없음')

    def test_missing_held_open_blocks_execution(self):
        with self.assertRaisesRegex(BacktestExecutionError, '보유 종목 시가 없음'):
            execute_rebalance(
                ranked_fixture(), {'999999': 1}, 0, {}, default_cost_model(),
            )


if __name__ == '__main__':
    unittest.main()
