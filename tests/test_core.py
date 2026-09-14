import unittest
import pandas as pd
from quantdesk.core import score_stocks, rebalance, DEFAULT_WEIGHTS


def universe():
    return pd.DataFrame([dict(code=f'{i:06}', name=f'Stock {i}', price=10000,
        momentum=40-i, value=40-i, quality=40-i, volatility=i+1,
        market_cap=200_000_000_000, turnover=2_000_000_000, eligible=True)
        for i in range(40)])


class CoreTests(unittest.TestCase):
    def test_scoring_and_filters(self):
        data = universe()
        data.loc[0, 'eligible'] = False
        data.loc[1, 'quality'] = float('nan')
        scores = score_stocks(data, DEFAULT_WEIGHTS)
        self.assertEqual(scores.iloc[0]['code'], '000002')
        self.assertEqual(len(scores), 38)
        self.assertTrue(scores.score.between(0, 100).all())

    def test_weights_must_sum_to_100(self):
        with self.assertRaises(ValueError):
            score_stocks(universe(), dict(momentum=0, value=0, quality=0, volatility=0))

    def test_buffer_and_exit(self):
        ranked = score_stocks(universe(), DEFAULT_WEIGHTS)
        holdings = {'000024': 100, '000035': 100}
        result = rebalance(ranked, holdings, 18_000_000, prices=universe().set_index('code').price.to_dict())
        self.assertIn('000024', result['targets'])
        self.assertNotIn('000035', result['targets'])
        self.assertEqual(len(result['targets']), 20)
        self.assertTrue(any(o['code'] == '000035' and o['side'] == '매도' for o in result['orders']))
        self.assertGreaterEqual(result['cash_after'], 0)

    def test_budget_and_minimum(self):
        ranked = score_stocks(universe(), DEFAULT_WEIGHTS)
        result = rebalance(ranked, {}, 1_000_000, prices=universe().set_index('code').price.to_dict())
        self.assertEqual(result['orders'], [])
        result = rebalance(ranked, {}, 20_000_000, prices=universe().set_index('code').price.to_dict())
        self.assertGreaterEqual(result['cash_after'], 0)
        self.assertTrue(all(o['quantity'] == int(o['quantity']) and o['amount'] >= 100000 for o in result['orders']))
        self.assertLessEqual(sum(o['amount'] + o['cost'] for o in result['orders']), 20_000_000)

    def test_missing_price_blocks(self):
        with self.assertRaises(ValueError):
            rebalance(score_stocks(universe(), DEFAULT_WEIGHTS), {'missing': 1}, 0, prices={})


if __name__ == '__main__':
    unittest.main()
