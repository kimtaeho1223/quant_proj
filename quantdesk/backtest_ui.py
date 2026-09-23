"""Streamlit workflow for readiness-first point-in-time backtests."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from quantdesk.backtest import BacktestConfig, run_backtest
from quantdesk.backtest_data import BacktestDataError, BacktestDataset, rank_week
from quantdesk.backtest_storage import BacktestStore
from quantdesk.dart import DartStore
from quantdesk.market import MarketStore


def load_backtest_dataset(market_path, dart_path):
    market = MarketStore(market_path)
    dart = DartStore(dart_path)
    with market.connect() as db:
        snapshot_dates = [row[0] for row in db.execute(
            'SELECT DISTINCT effective_date FROM market_snapshots ORDER BY effective_date'
        ).fetchall()]
        codes = [row[0] for row in db.execute(
            'SELECT DISTINCT code FROM market_snapshots ORDER BY code'
        ).fetchall()]
        calendar = [row[0] for row in db.execute(
            'SELECT DISTINCT date FROM market_prices ORDER BY date'
        ).fetchall()]

    snapshots = {day: market.historical_snapshot(day) for day in snapshot_dates}
    prices = {code: market.prices(code) for code in codes}
    indices = {symbol: market.index_prices(symbol) for symbol in ('KOSPI', 'KOSDAQ')}
    actions = market.corporate_actions('1900-01-01', '2100-12-31')
    return BacktestDataset(
        calendar=calendar,
        snapshots=snapshots,
        prices=prices,
        statements=dart.statements(),
        companies=dart.companies(),
        indices=indices,
        corporate_actions=actions,
    )


def _issue(source, message):
    return {'구분': source, '내용': message}


def readiness_report(dataset, config):
    issues = []
    if not dataset.snapshots:
        issues.append(_issue('종목군', '과거 종목군 스냅샷이 없습니다.'))
    if dataset.calendar.empty or not dataset.prices:
        issues.append(_issue('주가', '과거 일별 주가와 거래일 달력이 없습니다.'))
    if not dataset.statements:
        issues.append(_issue('재무', '공시일이 확인된 연간 재무제표가 없습니다.'))
    if not dataset.companies:
        issues.append(_issue('기업', 'KRX와 OpenDART 기업 대조 자료가 없습니다.'))
    for symbol in ('KOSPI', 'KOSDAQ'):
        if dataset.indices.get(symbol, pd.DataFrame()).empty:
            issues.append(_issue('지수', f'{symbol} 가격 자료가 없습니다.'))

    valid_weeks = 0
    invalid_weeks = 0
    schedule = pd.DataFrame(columns=['signal_date', 'execution_date'])
    if not issues:
        try:
            schedule = dataset.schedule(config.start, config.end)
            schedule = schedule[schedule.execution_date <= config.end].reset_index(drop=True)
            if schedule.empty:
                issues.append(_issue('기간', '실행 가능한 완료 주차가 없습니다.'))
            for row in schedule.itertuples(index=False):
                try:
                    week = rank_week(dataset, row.signal_date, config.minimum_ready)
                    blocking = [item for item in week['issues'] if item.startswith('유효 후보 ')]
                    if blocking:
                        invalid_weeks += 1
                        issues.extend(_issue(row.signal_date, item) for item in blocking)
                    else:
                        valid_weeks += 1
                except (BacktestDataError, ValueError) as exc:
                    invalid_weeks += 1
                    issues.append(_issue(row.signal_date, str(exc)))
        except (BacktestDataError, ValueError) as exc:
            issues.append(_issue('기간', str(exc)))

    sessions = dataset.calendar.date.tolist()
    before_start = [day for day in sessions if day < config.start]
    warmup_start = before_start[-252] if len(before_start) >= 252 else None
    return {
        'blocking': bool(issues) or invalid_weeks > 0,
        'issues': issues,
        'valid_weeks': valid_weeks,
        'invalid_weeks': invalid_weeks,
        'schedule': schedule,
        'warmup_start': warmup_start,
        'fingerprint': dataset.fingerprint(),
    }


def _percent(value):
    return f'{value * 100:,.2f}%'


def _display_frame(value):
    frame = value if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
    if frame.empty:
        st.info('표시할 기록이 없습니다.')
    else:
        st.dataframe(frame, hide_index=True, width='stretch')


def render_backtest_result(result, run_id):
    version = result.get('engine_version', '기록 없음')
    st.caption(f'실행 ID #{run_id} · 엔진 {version} · 입력 fingerprint {result["fingerprint"]}')
    st.warning('배당 제외 가격수익률입니다. 무위험수익률은 연 0%로 계산합니다.')
    if result['status'] != 'complete':
        st.error('백테스트가 완전하게 끝나지 않아 공식 성과 지표를 표시하지 않습니다.')
        _display_frame([{'내용': item} for item in result.get('issues', [])])
        return

    performance, portfolio, trades, audit = st.tabs(
        ['성과', '주간 포트폴리오', '거래 내역', '감사 기록']
    )
    metrics = result['metrics']
    with performance:
        columns = st.columns(6)
        values = [
            ('누적수익률', _percent(metrics['cumulative_return'])),
            ('CAGR', _percent(metrics['cagr'])),
            ('MDD', _percent(metrics['max_drawdown'])),
            ('연 변동성', _percent(metrics['annual_volatility'])),
            ('Sharpe', f'{metrics["sharpe"]:,.2f}'),
            ('총 거래비용', f'{result["weekly"].cost_total.sum():,.0f}원'),
        ]
        for column, (label, value) in zip(columns, values):
            column.metric(label, value)
        comparison = result['comparison'].copy()
        comparison['date'] = pd.to_datetime(comparison.date)
        st.line_chart(comparison.set_index('date'), y=[
            'strategy', 'top100_equal_weight', 'kospi', 'kosdaq',
        ])
        turnover = result['weekly'].turnover_amount.sum()
        st.metric('누적 회전금액', f'{turnover:,.0f}원')
        st.caption('상위 100 동일비중 벤치마크는 선택 효과 비교를 위해 소수점 수량으로 계산합니다.')
    with portfolio:
        _display_frame(result['weekly'])
    with trades:
        _display_frame(result['trades'])
    with audit:
        weeks = result.get('audit', [])
        if not weeks:
            st.info('감사 기록이 없습니다.')
        else:
            labels = [item.get('signal_date', f'{index + 1}주차') for index, item in enumerate(weeks)]
            selected = st.selectbox('검토 주차', labels, key=f'backtest_audit_week_{run_id}')
            evidence = weeks[labels.index(selected)]
            st.subheader('선정 순위와 보유 근거')
            _display_frame(evidence.get('ranked', []))
            st.subheader('사용한 공시')
            _display_frame(evidence.get('filings', []))
            st.subheader('제외 종목과 사유')
            _display_frame(evidence.get('exclusions', []))
            st.subheader('주문과 미체결')
            _display_frame(evidence.get('orders', []))
            _display_frame(evidence.get('unfilled', []))


def _default_dates(dataset):
    today = date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=365 * 3)
    if dataset.snapshots:
        start = pd.Timestamp(min(dataset.snapshots)).date()
    if not dataset.calendar.empty:
        end = pd.Timestamp(dataset.calendar.date.max()).date()
    return start, end


def render_backtest(market_path, dart_path, backtest_path):
    st.subheader('시점고정 멀티팩터 백테스트')
    st.caption('과거에 실제로 알 수 있었던 종목군·가격·공시만 사용합니다. 실제 주문은 전송되지 않습니다.')
    dataset = load_backtest_dataset(market_path, dart_path)
    default_start, default_end = _default_dates(dataset)
    left, right = st.columns(2)
    start = left.date_input('성과 시작일', value=default_start, key='backtest_start')
    end = right.date_input('성과 종료일', value=default_end, key='backtest_end')
    try:
        config = BacktestConfig(start.isoformat(), end.isoformat())
        readiness = readiness_report(dataset, config)
    except ValueError as exc:
        st.error(str(exc))
        return

    st.subheader('실행 전 점검')
    a, b, c, d = st.columns(4)
    a.metric('초기자금', f'{config.initial_cash:,.0f}원')
    b.metric('유효 주차', f'{readiness["valid_weeks"]}주')
    c.metric('무효 주차', f'{readiness["invalid_weeks"]}주')
    d.metric('최소 후보', f'{config.minimum_ready}종목')
    st.caption(
        '매주 마지막 거래일 종가로 신호 생성 · 다음 거래일 시가 체결 · 상위 20종목 동일비중 · '
        '30위 유지 · 정수 수량 · 최소 주문 10만원 · 연간 재무제표'
    )
    warmup = readiness['warmup_start'] or '252거래일 자료 부족'
    st.write(f'요청 성과기간: {config.start} ~ {config.end} · 워밍업 시작: {warmup}')
    st.code(readiness['fingerprint'], language=None)
    st.caption('위 SHA-256 fingerprint는 이번 실행에 사용될 전체 입력 자료를 식별합니다.')
    if readiness['blocking']:
        messages = ' '.join(item['내용'] for item in readiness['issues'])
        st.warning(f'실행 전 필요한 데이터가 부족합니다. {messages}')
        _display_frame(readiness['issues'])
    else:
        st.success('모든 주차가 엄격한 80종목 기준을 통과했습니다.')

    run_clicked = st.button(
        '백테스트 실행', key='run_backtest', type='primary',
        icon=':material/play_arrow:', disabled=readiness['blocking'],
    )
    store = BacktestStore(backtest_path)
    if run_clicked:
        with st.spinner('시점고정 백테스트를 실행하고 있습니다.'):
            result = run_backtest(dataset, config)
            run_id = store.save_run(result)
            st.session_state['backtest_run_id'] = run_id
    run_id = st.session_state.get('backtest_run_id')
    if run_id:
        try:
            render_backtest_result(store.load_run(run_id), run_id)
        except ValueError:
            st.session_state.pop('backtest_run_id', None)
