import math
import pandas as pd

DEFAULT_WEIGHTS = dict(momentum=45, value=20, quality=20, volatility=15)


def score_stocks(data, weights):
    if set(weights) != set(DEFAULT_WEIGHTS) or any(not math.isfinite(v) or v < 0 for v in weights.values()) or sum(weights.values()) != 100:
        raise ValueError('팩터 비중의 합계는 100이어야 합니다.')
    fields = ['price', 'momentum', 'value', 'quality', 'volatility', 'market_cap', 'turnover']
    clean = data.replace([float('inf'), -float('inf')], float('nan')).dropna(subset=fields).copy()
    clean = clean[clean.eligible & (clean.price > 0) & (clean.market_cap >= 100_000_000_000) & (clean.turnover >= 1_000_000_000)].copy()
    clean['score'] = 0.0
    for factor, weight in weights.items():
        clean[f'{factor}_score'] = clean[factor].rank(pct=True, ascending=factor != 'volatility') * 100
        clean['score'] += clean[f'{factor}_score'] * weight / 100
    clean = clean.sort_values(['score', 'code'], ascending=[False, True]).reset_index(drop=True)
    clean['rank'] = clean.index + 1
    return clean


def select_targets(ranked, held_codes, target_count=20, retention_rank=30):
    if target_count < 0 or retention_rank < 0:
        raise ValueError('목표 종목 수와 유지 순위는 0 이상이어야 합니다.')
    held = set(held_codes)
    kept = ranked[(ranked['rank'] <= retention_rank) & ranked.code.isin(held)]
    kept = kept.head(target_count).code.tolist()
    fill = [code for code in ranked.code.tolist() if code not in kept]
    return kept + fill[:target_count - len(kept)]


def rebalance(ranked, holdings, cash, prices, min_trade=100_000, fee_rate=0.003):
    if not math.isfinite(cash) or cash < 0 or not 0 <= fee_rate < 1 or min_trade < 0:
        raise ValueError('현금과 거래비용 설정을 확인해 주세요.')
    if any(not math.isfinite(q) or q < 0 or int(q) != q for q in holdings.values()):
        raise ValueError('보유 수량은 0 이상의 정수여야 합니다.')
    holdings = {code: int(q) for code, q in holdings.items() if q > 0}
    prices = {**prices, **ranked.set_index('code').price.to_dict()}
    if any(code not in prices or not math.isfinite(prices[code]) or prices[code] <= 0 for code in holdings):
        raise ValueError('보유 종목의 유효한 가격이 없어 매매 제안을 계산할 수 없습니다.')
    targets = select_targets(ranked, holdings)
    equity = cash + sum(prices[c] * q for c, q in holdings.items())
    # Reserve costs up front; unused allocations stay in cash if fewer than 20 names qualify.
    allocation = equity / 20 / (1 + fee_rate)
    desired = {c: math.floor(allocation / prices[c]) for c in targets}
    remaining = dict(holdings)
    orders, skipped = [], []
    available = float(cash)
    names = ranked.set_index('code').name.to_dict()
    for selling in (True, False):
        for code in sorted(set(holdings) | set(targets), key=lambda c: (targets.index(c) if c in targets else -1, c)):
            delta = desired.get(code, 0) - holdings.get(code, 0)
            if delta == 0 or (delta < 0) != selling:
                continue
            quantity = abs(delta)
            if not selling:
                quantity = min(quantity, math.floor((available + 1e-8) / (prices[code] * (1 + fee_rate))))
            amount = quantity * prices[code]
            if amount < min_trade or quantity == 0:
                skipped.append(dict(code=code, reason='최소 거래금액 미만 또는 현금 부족'))
                continue
            cost = amount * fee_rate
            available += amount - cost if selling else -amount - cost
            remaining[code] = remaining.get(code, 0) + (-quantity if selling else quantity)
            orders.append(dict(code=code, name=names.get(code, code), side='매도' if selling else '매수', quantity=int(quantity), price=prices[code], amount=amount, cost=cost))
    holdings_after = {c: q for c, q in remaining.items() if q > 0}
    invested_after = sum(prices[c] * q for c, q in holdings_after.items())
    ranked_by_code = ranked.set_index('code')
    target_positions = []
    for code in targets:
        row = ranked_by_code.loc[code]
        amount = prices[code] * holdings_after.get(code, 0)
        target_positions.append(dict(
            rank=int(row['rank']), code=code, name=str(row['name']), sector=str(row.get('sector', '')),
            price=float(prices[code]), target_weight=5.0, quantity=holdings_after.get(code, 0),
            amount=float(amount), actual_weight=float(amount / equity * 100 if equity else 0),
        ))
    turnover = sum(order['amount'] for order in orders) / equity * 100 if equity else 0
    unfunded_targets = [code for code in targets if desired[code] == 0]
    return dict(targets=targets, unfunded_targets=unfunded_targets, target_positions=target_positions,
                orders=orders, skipped=skipped,
                cash_after=max(0, available), equity=equity, invested_after=invested_after, turnover=turnover,
                holdings_after=holdings_after)
