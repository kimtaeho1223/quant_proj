"""Weekly point-in-time backtest orchestration."""
from dataclasses import asdict, dataclass
import json
import math

import pandas as pd

from quantdesk.backtest_data import BacktestDataError, rank_week
from quantdesk.backtest_execution import (
    BacktestExecutionError,
    execute_rebalance,
    trade_cost,
    value_portfolio,
)
from quantdesk.backtest_metrics import MetricError, build_comparison, performance_metrics
from quantdesk.real_ranking import candidate_universe, select_statement


ENGINE_VERSION = '0.4.0'


@dataclass(frozen=True)
class BacktestConfig:
    start: str
    end: str
    initial_cash: int = 10_000_000
    minimum_ready: int = 80
    min_trade: int = 100_000
    fee_rate: float = 0.003
    tax_rate: float = 0.0
    slippage_rate: float = 0.0

    def __post_init__(self):
        try:
            start = pd.Timestamp(self.start).date().isoformat()
            end = pd.Timestamp(self.end).date().isoformat()
        except (TypeError, ValueError):
            raise ValueError('백테스트 기간을 확인해 주세요.') from None
        if start > end:
            raise ValueError('시작일은 종료일보다 늦을 수 없습니다.')
        if self.initial_cash <= 0 or self.min_trade < 0 or not 1 <= self.minimum_ready <= 100:
            raise ValueError('초기자금과 데이터 준비 기준을 확인해 주세요.')
        rates = (self.fee_rate, self.tax_rate, self.slippage_rate)
        if any(not math.isfinite(rate) or rate < 0 or rate >= 1 for rate in rates):
            raise ValueError('거래비용률은 0 이상 1 미만이어야 합니다.')
        object.__setattr__(self, 'start', start)
        object.__setattr__(self, 'end', end)


def _records(frame):
    if frame is None or frame.empty:
        return []
    return json.loads(frame.to_json(orient='records', date_format='iso'))


def _cashflows(frame):
    columns = ['date', 'amount', 'kind', 'code']
    if frame is None:
        return pd.DataFrame(columns=columns)
    if not isinstance(frame, pd.DataFrame) or not set(columns).issubset(frame.columns):
        raise ValueError('외부 현금흐름 열을 확인해 주세요.')
    data = frame[columns].copy()
    data['date'] = pd.to_datetime(data.date, errors='coerce').dt.strftime('%Y-%m-%d')
    data['amount'] = pd.to_numeric(data.amount, errors='coerce')
    if data[['date', 'amount']].isna().any().any() or not data.amount.map(math.isfinite).all():
        raise ValueError('외부 현금흐름 값이 유효하지 않습니다.')
    return data.sort_values(['date', 'kind', 'code']).reset_index(drop=True)


def _empty_result(dataset, config, status='incomplete', issues=None, nav=None,
                  weekly=None, trades=None, audit=None):
    return {
        'status': status,
        'engine_version': ENGINE_VERSION,
        'config': asdict(config),
        'fingerprint': dataset.fingerprint(),
        'nav': pd.DataFrame(nav or [], columns=[
            'date', 'cash', 'positions_value', 'equity', 'external_flow', 'carried_prices',
        ]),
        'weekly': pd.DataFrame(weekly or []),
        'trades': pd.DataFrame(trades or []),
        'audit': audit or [],
        'issues': issues or [],
        'benchmark': {},
    }


def _price_on(dataset, code, date, field):
    frame = dataset.prices.get(code)
    if frame is None:
        return None
    row = frame[frame.Date == date]
    if row.empty:
        return None
    return float(row[field].iloc[-1])


def _blocking_actions(dataset, start, end):
    actions = dataset.corporate_actions
    if actions.empty:
        return []
    required = {'code', 'effective_date', 'action_type', 'status'}
    if not required.issubset(actions.columns):
        return [{'reason': '기업행사 필수 열 누락'}]
    dates = pd.to_datetime(actions.effective_date, errors='coerce').dt.strftime('%Y-%m-%d')
    mask = dates.between(start, end) & actions.status.ne('validated')
    return _records(actions.loc[mask].assign(effective_date=dates.loc[mask]))


def _run_fractional_benchmark(dataset, config, schedule, cost_model):
    holdings = {}
    cash = float(config.initial_cash)
    last_valid = {}
    nav_rows = [{
        'date': config.start, 'cash': cash, 'positions_value': 0.0,
        'equity': cash, 'external_flow': 0.0, 'carried_prices': 0,
    }]
    trade_rows = []
    weekly_rows = []
    issues = []
    for index, row in schedule.iterrows():
        signal = row.signal_date
        execution_date = row.execution_date
        listing = candidate_universe(dataset.snapshots[signal])
        targets = listing.Code.tolist()
        opens = {code: _price_on(dataset, code, execution_date, 'Open') for code in targets}
        for code in holdings:
            opens[code] = _price_on(dataset, code, execution_date, 'Open')
        missing = [code for code, price in opens.items() if price is None]
        if missing:
            issues.append(f'{signal}: 벤치마크 시가 없음 {len(missing)}종목')
            return {
                'status': 'incomplete', 'nav': pd.DataFrame(nav_rows),
                'weekly': pd.DataFrame(weekly_rows), 'trades': pd.DataFrame(trade_rows),
                'issues': issues, 'cost_model': dict(cost_model), 'method': 'fractional_equal_weight',
            }

        equity = cash + sum(holdings[code] * opens[code] for code in holdings)
        target_value = equity / len(targets)
        desired = {code: target_value / opens[code] for code in targets}
        trades = []
        for code in sorted(set(holdings) | set(targets)):
            delta = desired.get(code, 0.0) - holdings.get(code, 0.0)
            if delta >= -1e-12:
                continue
            quantity = -delta
            gross = quantity * opens[code]
            costs = trade_cost(gross, '매도', cost_model)
            cash += gross - costs['total']
            holdings[code] = holdings.get(code, 0.0) - quantity
            trades.append((code, '매도', quantity, gross, costs))

        planned_buys = {
            code: max(0.0, desired[code] - holdings.get(code, 0.0))
            for code in targets
        }
        buy_gross = sum(quantity * opens[code] for code, quantity in planned_buys.items())
        buy_rate = cost_model['fee_rate'] + cost_model['slippage_rate']
        scale = min(1.0, cash / (buy_gross * (1 + buy_rate))) if buy_gross > 0 else 0.0
        for code in targets:
            quantity = planned_buys[code] * scale
            if quantity <= 1e-12:
                continue
            gross = quantity * opens[code]
            costs = trade_cost(gross, '매수', cost_model)
            cash -= gross + costs['total']
            holdings[code] = holdings.get(code, 0.0) + quantity
            trades.append((code, '매수', quantity, gross, costs))
        if -1e-6 < cash < 0:
            cash = 0.0
        elif cash < 0:
            raise BacktestExecutionError('벤치마크 현금이 음수입니다.')
        holdings = {code: quantity for code, quantity in holdings.items() if quantity > 1e-12}
        for code in holdings:
            close = _price_on(dataset, code, signal, 'Close')
            if close is not None:
                last_valid[code] = close
        for code, side, quantity, gross, costs in trades:
            trade_rows.append({
                'signal_date': signal, 'execution_date': execution_date,
                'code': code, 'side': side, 'quantity': quantity,
                'price': opens[code], 'gross_amount': gross,
                'fee': costs['fee'], 'tax': costs['tax'],
                'slippage': costs['slippage'], 'cost_total': costs['total'],
                'price_source': 'open',
            })
        weekly_rows.append({
            'signal_date': signal, 'execution_date': execution_date,
            'holding_count': len(holdings), 'cash_after': cash,
            'turnover_amount': sum(item[3] for item in trades),
            'cost_total': sum(item[4]['total'] for item in trades),
        })

        next_execution = schedule.iloc[index + 1].execution_date if index + 1 < len(schedule) else None
        valuation_dates = [
            day for day in dataset.calendar.date
            if execution_date <= day <= config.end
            and (next_execution is None or day < next_execution)
        ]
        for date in valuation_dates:
            closes = {
                code: price for code in holdings
                if (price := _price_on(dataset, code, date, 'Close')) is not None
            }
            valuation = value_portfolio(holdings, cash, closes, last_valid)
            last_valid = valuation['last_valid_prices']
            nav_rows.append({
                'date': date, 'cash': cash,
                'positions_value': valuation['positions_value'],
                'equity': valuation['equity'], 'external_flow': 0.0,
                'carried_prices': sum(source == 'carried' for source in valuation['price_sources'].values()),
            })
    return {
        'status': 'complete', 'nav': pd.DataFrame(nav_rows),
        'weekly': pd.DataFrame(weekly_rows), 'trades': pd.DataFrame(trade_rows),
        'issues': issues, 'cost_model': dict(cost_model), 'method': 'fractional_equal_weight',
    }


def run_backtest(dataset, config, external_cashflows=None):
    flows = _cashflows(external_cashflows)
    schedule = dataset.schedule(config.start, config.end)
    schedule = schedule[schedule.execution_date <= config.end].reset_index(drop=True)
    if schedule.empty:
        return _empty_result(dataset, config, issues=['실행 가능한 완료 주차가 없습니다.'])

    first_signal = schedule.iloc[0].signal_date
    signal_history = dataset.calendar[dataset.calendar.date <= first_signal].date.tolist()
    validation_start = signal_history[-252] if len(signal_history) >= 252 else signal_history[0]
    blocking_actions = _blocking_actions(dataset, validation_start, config.end)
    if blocking_actions:
        return _empty_result(
            dataset, config,
            issues=[f'검증되지 않은 기업행사: {item.get("code", "알 수 없음")}'
                    for item in blocking_actions],
            audit=[{'blocking_corporate_actions': blocking_actions}],
        )

    holdings = {}
    cash = float(config.initial_cash)
    last_valid = {}
    nav_rows = [{
        'date': config.start,
        'cash': cash,
        'positions_value': 0.0,
        'equity': cash,
        'external_flow': 0.0,
        'carried_prices': 0,
    }]
    weekly_rows = []
    trade_rows = []
    audit_rows = []
    issues = []
    cost_model = {
        'fee_rate': config.fee_rate,
        'tax_rate': config.tax_rate,
        'slippage_rate': config.slippage_rate,
    }

    for index, schedule_row in schedule.iterrows():
        signal = schedule_row.signal_date
        execution_date = schedule_row.execution_date
        try:
            week = rank_week(dataset, signal, minimum_ready=config.minimum_ready)
        except (BacktestDataError, ValueError) as exc:
            issues.append(f'{signal}: {exc}')
            return _empty_result(dataset, config, issues=issues, nav=nav_rows,
                                 weekly=weekly_rows, trades=trade_rows, audit=audit_rows)

        blocking = [issue for issue in week['issues'] if issue.startswith('유효 후보 ')]
        if blocking:
            issues.extend(f'{signal}: {issue}' for issue in blocking)
            audit_rows.append({
                'signal_date': signal,
                'execution_date': execution_date,
                'issues': list(week['issues']),
                'ranked': _records(week['ranked']),
                'exclusions': _records(week['exclusions']),
                'filings': [],
                'unfilled': [],
            })
            return _empty_result(dataset, config, issues=issues, nav=nav_rows,
                                 weekly=weekly_rows, trades=trade_rows, audit=audit_rows)

        execution_open = dict(week['execution_open'])
        for code in holdings:
            price = _price_on(dataset, code, execution_date, 'Open')
            if price is not None:
                execution_open[code] = price
        try:
            execution = execute_rebalance(
                week['ranked'], holdings, cash, execution_open,
                cost_model, min_trade=config.min_trade,
            )
        except BacktestExecutionError as exc:
            issues.append(f'{signal}: {exc}')
            return _empty_result(dataset, config, issues=issues, nav=nav_rows,
                                 weekly=weekly_rows, trades=trade_rows, audit=audit_rows)

        holdings = execution['holdings_after']
        cash = execution['cash_after']
        for code in holdings:
            close = _price_on(dataset, code, signal, 'Close')
            if close is not None:
                last_valid[code] = close

        annotated_orders = []
        for order in execution['orders']:
            annotated = dict(order)
            annotated.update({
                'signal_date': signal,
                'execution_date': execution_date,
                'price_source': 'open',
            })
            annotated_orders.append(annotated)
            trade_rows.append(annotated)

        filings = []
        for code in week['ranked'].code.tolist():
            selected = select_statement(dataset.statements, code, signal)
            if selected:
                filings.append({
                    'stock': code,
                    'year': selected['year'],
                    'basis': selected['basis'],
                    'receipt': selected['receipt'],
                    'filed_at': selected['filed_at'],
                })
        audit_rows.append({
            'signal_date': signal,
            'execution_date': execution_date,
            'issues': list(week['issues']),
            'ranked': _records(week['ranked']),
            'exclusions': _records(week['exclusions']),
            'filings': filings,
            'targets': list(execution['targets']),
            'orders': annotated_orders,
            'unfilled': list(execution['unfilled']),
        })
        weekly_rows.append({
            'signal_date': signal,
            'execution_date': execution_date,
            'candidate_count': len(week['ranked']),
            'holding_count': len(holdings),
            'cash_after': cash,
            'equity_before': execution['equity_before'],
            'turnover_amount': execution['turnover_amount'],
            'cost_total': execution['costs']['cost_total'],
            'holdings': json.dumps(holdings, sort_keys=True),
        })

        next_execution = (
            schedule.iloc[index + 1].execution_date
            if index + 1 < len(schedule) else None
        )
        valuation_dates = [
            day for day in dataset.calendar.date
            if execution_date <= day <= config.end
            and (next_execution is None or day < next_execution)
        ]
        for date in valuation_dates:
            daily_flows = flows[flows.date == date]
            external_flow = float(daily_flows.amount.sum()) if not daily_flows.empty else 0.0
            cash += external_flow
            closes = {
                code: price for code in holdings
                if (price := _price_on(dataset, code, date, 'Close')) is not None
            }
            try:
                valuation = value_portfolio(holdings, cash, closes, last_valid)
            except BacktestExecutionError as exc:
                issues.append(f'{date}: {exc}')
                return _empty_result(dataset, config, issues=issues, nav=nav_rows,
                                     weekly=weekly_rows, trades=trade_rows, audit=audit_rows)
            last_valid = valuation['last_valid_prices']
            nav_rows.append({
                'date': date,
                'cash': cash,
                'positions_value': valuation['positions_value'],
                'equity': valuation['equity'],
                'external_flow': external_flow,
                'carried_prices': sum(source == 'carried' for source in valuation['price_sources'].values()),
            })

    result = _empty_result(
        dataset, config, status='complete', issues=issues,
        nav=nav_rows, weekly=weekly_rows, trades=trade_rows, audit=audit_rows,
    )
    benchmark = _run_fractional_benchmark(dataset, config, schedule, cost_model)
    result['benchmark'] = benchmark
    if benchmark['status'] != 'complete':
        result['status'] = 'incomplete'
        result['issues'].extend(benchmark['issues'])
        return result
    try:
        result['metrics'] = performance_metrics(result['nav'], risk_free_rate=0.0)
        result['benchmark_metrics'] = performance_metrics(benchmark['nav'], risk_free_rate=0.0)
        result['comparison'] = build_comparison(
            result['nav'], benchmark['nav'],
            dataset.indices.get('KOSPI', pd.DataFrame()),
            dataset.indices.get('KOSDAQ', pd.DataFrame()),
        )
    except MetricError as exc:
        result['status'] = 'incomplete'
        result['issues'].append(f'성과 지표 계산 실패: {exc}')
    return result
