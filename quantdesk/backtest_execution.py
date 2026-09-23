"""Deterministic whole-share execution and daily portfolio valuation."""
import math

from quantdesk.core import select_targets


class BacktestExecutionError(ValueError):
    pass


def default_cost_model():
    return {'fee_rate': 0.003, 'tax_rate': 0.0, 'slippage_rate': 0.0}


def _cost_model(model):
    required = {'fee_rate', 'tax_rate', 'slippage_rate'}
    if set(model) != required:
        raise BacktestExecutionError('비용 모델 항목을 확인해 주세요.')
    result = {key: float(model[key]) for key in required}
    if any(not math.isfinite(value) or value < 0 or value >= 1 for value in result.values()):
        raise BacktestExecutionError('비용률은 0 이상 1 미만이어야 합니다.')
    return result


def trade_cost(amount, side, model):
    fee = amount * model['fee_rate']
    tax = amount * model['tax_rate'] if side == '매도' else 0.0
    slippage = amount * model['slippage_rate']
    return {
        'fee': fee,
        'tax': tax,
        'slippage': slippage,
        'total': fee + tax + slippage,
    }


def _valid_price(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def execute_rebalance(ranked, holdings, cash, open_prices, cost_model, min_trade=100_000):
    model = _cost_model(cost_model)
    if not math.isfinite(cash) or cash < 0 or min_trade < 0:
        raise BacktestExecutionError('현금과 최소 주문금액을 확인해 주세요.')
    if any(not math.isfinite(quantity) or quantity < 0 or int(quantity) != quantity
           for quantity in holdings.values()):
        raise BacktestExecutionError('보유 수량은 0 이상의 정수여야 합니다.')
    positions = {str(code): int(quantity) for code, quantity in holdings.items() if quantity > 0}
    opens = {str(code): _valid_price(price) for code, price in open_prices.items()}
    missing_held = [code for code in positions if opens.get(code) is None]
    if missing_held:
        raise BacktestExecutionError(f"보유 종목 시가 없음: {', '.join(sorted(missing_held))}")

    targets = select_targets(ranked, positions)
    equity = float(cash + sum(opens[code] * quantity for code, quantity in positions.items()))
    allocation = equity / 20
    desired = {}
    unfilled = []
    buy_rate = model['fee_rate'] + model['slippage_rate']
    for code in targets:
        price = opens.get(code)
        if price is None:
            unfilled.append({'code': code, 'side': '매수', 'reason': '다음 거래일 시가 없음'})
            continue
        quantity = math.floor(allocation / (price * (1 + buy_rate)))
        desired[code] = quantity
        if quantity == 0:
            unfilled.append({'code': code, 'side': '매수', 'reason': '목표 배정액으로 1주 매수 불가'})

    ranks = ranked.set_index('code')['rank'].to_dict() if not ranked.empty else {}
    names = ranked.set_index('code')['name'].to_dict() if 'name' in ranked else {}
    order_codes = sorted(
        set(positions) | set(targets),
        key=lambda code: (ranks.get(code, -1), code),
    )
    orders = []
    available = float(cash)

    def record_order(code, side, quantity):
        nonlocal available
        price = opens[code]
        gross = float(price * quantity)
        costs = trade_cost(gross, side, model)
        delta = gross - costs['total'] if side == '매도' else -gross - costs['total']
        available += delta
        positions[code] = positions.get(code, 0) + (-quantity if side == '매도' else quantity)
        orders.append({
            'code': code,
            'name': names.get(code, code),
            'side': side,
            'quantity': int(quantity),
            'price': float(price),
            'gross_amount': gross,
            'fee': costs['fee'],
            'tax': costs['tax'],
            'slippage': costs['slippage'],
            'cost_total': costs['total'],
            'cash_delta': delta,
        })

    for code in order_codes:
        delta = desired.get(code, 0) - positions.get(code, 0)
        if delta >= 0:
            continue
        quantity = -delta
        amount = quantity * opens[code]
        if amount < min_trade:
            unfilled.append({'code': code, 'side': '매도', 'reason': '최소 주문금액 미만'})
            continue
        record_order(code, '매도', quantity)

    for code in order_codes:
        if code not in desired:
            continue
        delta = desired[code] - positions.get(code, 0)
        if delta <= 0:
            continue
        price = opens[code]
        affordable = math.floor((available + 1e-8) / (price * (1 + buy_rate)))
        quantity = min(delta, affordable)
        if quantity == 0:
            unfilled.append({'code': code, 'side': '매수', 'reason': '현금 부족'})
            continue
        amount = quantity * price
        if amount < min_trade:
            unfilled.append({'code': code, 'side': '매수', 'reason': '최소 주문금액 미만'})
            continue
        record_order(code, '매수', quantity)

    positions = {code: quantity for code, quantity in positions.items() if quantity > 0}
    totals = {
        key: sum(order[key] for order in orders)
        for key in ('fee', 'tax', 'slippage', 'cost_total')
    }
    return {
        'targets': targets,
        'holdings_after': positions,
        'cash_after': max(0.0, available),
        'orders': orders,
        'unfilled': unfilled,
        'costs': totals,
        'turnover_amount': sum(order['gross_amount'] for order in orders),
        'equity_before': equity,
    }


def value_portfolio(holdings, cash, close_prices, last_valid_prices):
    if not math.isfinite(cash) or cash < 0:
        raise BacktestExecutionError('평가 현금이 유효하지 않습니다.')
    used = {}
    sources = {}
    updated = dict(last_valid_prices)
    for code, quantity in holdings.items():
        current = _valid_price(close_prices.get(code))
        carried = _valid_price(last_valid_prices.get(code))
        price = current if current is not None else carried
        if price is None:
            raise BacktestExecutionError(f'{code}: 평가 종가 없음')
        used[code] = price
        sources[code] = 'current' if current is not None else 'carried'
        if current is not None:
            updated[code] = current
    positions_value = float(sum(used[code] * quantity for code, quantity in holdings.items()))
    return {
        'equity': float(cash + positions_value),
        'positions_value': positions_value,
        'prices': used,
        'price_sources': sources,
        'last_valid_prices': updated,
    }
