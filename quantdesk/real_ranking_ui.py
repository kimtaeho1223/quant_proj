"""Streamlit view for the locally computed multifactor research ranking."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from quantdesk.core import rebalance
from quantdesk.dart import DartStore
from quantdesk.market import MarketStore
from quantdesk.real_ranking import build_ranking, candidate_universe
from quantdesk.storage import Store


RANK_COLUMNS = {
    'rank': '순위', 'code': '종목코드', 'name': '종목명', 'sector': '업종', 'score': '종합 점수',
    'price': '종가 (원)', 'momentum': '12개월 수익률 (%)', 'value': '이익수익률 (%)',
    'quality': 'ROE (%)', 'volatility': '연 변동성 (%)', 'market_cap': '시가총액 (원)',
    'turnover': '거래대금 (원)', 'price_date': '주가 기준일', 'filing_date': '재무 공시일',
    'statement_basis': '재무제표 기준', 'Code': '종목코드', 'Name': '종목명', '제외 사유': '제외 사유',
}
PORTFOLIO_COLUMNS = {
    'rank': '순위', 'code': '종목코드', 'name': '종목명', 'sector': '업종', 'price': '현재가 (원)',
    'target_weight': '목표 비중 (%)', 'actual_weight': '예상 비중 (%)', 'quantity': '목표 수량',
    'amount': '예상 평가금액 (원)', 'side': '구분', 'cost': '비용 여유분 (원)', 'reason': '사유',
}


def _latest_prices(prices_by_code, as_of):
    latest = {}
    cutoff = pd.Timestamp(as_of)
    for code, frame in prices_by_code.items():
        if frame.empty:
            continue
        rows = frame.copy()
        rows['Date'] = pd.to_datetime(rows['Date'], errors='coerce')
        rows['Close'] = pd.to_numeric(rows['Close'], errors='coerce')
        rows = rows[(rows['Date'] <= cutoff) & (rows['Close'] > 0)].sort_values('Date')
        if not rows.empty:
            latest[code] = float(rows.iloc[-1]['Close'])
    return latest


def account_editor_rows(candidates, listing, holdings, prices):
    rows = candidates[['Code', 'Name']].rename(columns={'Code': 'code', 'Name': 'name'}).copy()
    current_codes = set(rows['code'])
    names = listing.set_index('Code')['Name'].to_dict()
    extras = [code for code in holdings if code not in current_codes]
    if extras:
        rows = pd.concat([
            rows,
            pd.DataFrame([{'code': code, 'name': names.get(code, code)} for code in extras]),
        ], ignore_index=True)
    rows['price'] = rows['code'].map(prices)
    rows['quantity'] = rows['code'].map(holdings).fillna(0).astype(int)
    return rows


def render_real_ranking(market_path, dart_path, portfolio_path):
    st.subheader('실제 멀티팩터 순위')
    st.caption('저장된 KRX 주가와 OpenDART 연간 재무제표로 계산합니다. 실제 주문은 전송되지 않습니다.')
    market = MarketStore(market_path)
    dart = DartStore(dart_path)
    listing, fetched = market.listing()
    if listing.empty:
        st.info('실제 주가 데이터 화면에서 종목 목록을 먼저 갱신해 주세요.')
        return

    default_date = datetime.now(ZoneInfo('Asia/Seoul')).date() - timedelta(days=1)
    as_of = st.date_input('기준일', value=default_date, max_value=default_date)
    candidates = candidate_universe(listing)
    prices_by_code = {code: market.prices(code) for code in candidates.Code}
    ranked, exclusions = build_ranking(listing, prices_by_code, dart.statements(), dart.companies(), as_of)

    a, b, c, d = st.columns(4)
    a.metric('후보 종목', f'{len(candidates)} / 100')
    b.metric('순위 산출', f'{len(ranked)}종목')
    c.metric('제외', f'{len(exclusions)}종목')
    d.metric('종목 목록 갱신', fetched[:10] if fetched else '없음')

    ranking_tab, portfolio_tab, orders_tab = st.tabs(['멀티팩터 순위', '주간 포트폴리오', '매매 제안'])
    with ranking_tab:
        if ranked.empty:
            st.warning('현재 기준일에 순위를 계산할 수 있는 종목이 없습니다.')
            st.caption('각 후보에 252거래일 이상 주가와 기준일 이전에 공시된, 기업 대조가 통과한 연간 재무제표가 필요합니다.')
        else:
            st.dataframe(
                ranked[['rank', 'code', 'name', 'sector', 'score', 'price', 'momentum', 'value', 'quality', 'volatility',
                        'price_date', 'filing_date', 'statement_basis']].round(2).rename(columns=RANK_COLUMNS),
                hide_index=True, width='stretch',
            )
            st.caption('점수는 후보군 안에서 모멘텀 45%, 가치 20%, ROE 20%, 저변동성 15%의 백분위 순위를 합산합니다.')
        if not exclusions.empty:
            with st.expander(f'제외 종목과 사유 ({len(exclusions)}종목)'):
                st.dataframe(exclusions.rename(columns=RANK_COLUMNS), hide_index=True, width='stretch')

    if ranked.empty:
        with portfolio_tab:
            st.info('순위가 산출되면 1,000만원 기준 주간 포트폴리오를 계산합니다.')
        with orders_tab:
            st.info('순위와 보유 내역이 준비되면 매매 제안을 계산합니다.')
        return

    store = Store(portfolio_path)
    holdings, cash = store.load_account()
    for code in holdings:
        if code not in prices_by_code:
            prices_by_code[code] = market.prices(code)
    prices = _latest_prices(prices_by_code, as_of)

    with portfolio_tab:
        st.caption('상위 20종목 동일 비중 · 30위 이내 기존 종목 유지 · 최소 주문 10만원 · 거래비용 여유분 0.3%')
        with st.form('actual_portfolio_account'):
            new_cash = st.number_input('보유 현금 (원)', min_value=0, value=int(cash), step=100_000, key='actual_cash')
            editor = account_editor_rows(candidates, listing, holdings, prices)
            edited = st.data_editor(
                editor, hide_index=True, width='stretch', disabled=['code', 'name', 'price'], key='actual_holdings_editor',
                column_config={
                    'code': st.column_config.TextColumn('종목코드'),
                    'name': st.column_config.TextColumn('종목명'),
                    'price': st.column_config.NumberColumn('현재가 (원)', format='%.0f'),
                    'quantity': st.column_config.NumberColumn('보유 수량', min_value=0, step=1, required=True),
                },
            )
            if st.form_submit_button('보유 내역 저장', icon=':material/save:', type='primary'):
                quantities = edited['quantity']
                if quantities.isna().any() or (quantities < 0).any() or (quantities % 1 != 0).any():
                    st.error('보유 수량은 0 이상의 정수로 입력해 주세요.')
                else:
                    store.save_account({row.code: int(row.quantity) for row in edited.itertuples() if row.quantity > 0}, new_cash)
                    st.session_state['actual_account_saved'] = True
                    st.rerun()
        if st.session_state.pop('actual_account_saved', False):
            st.success('실제 포트폴리오의 보유 내역을 저장했습니다.')

        try:
            result = rebalance(ranked, holdings, cash, prices, fee_rate=0.003)
        except ValueError as exc:
            st.error(str(exc))
            result = None

        if result:
            a, b, c, d = st.columns(4)
            a.metric('총 평가금액', f"{result['equity']:,.0f}원")
            funded = len(result['targets']) - len(result['unfunded_targets'])
            b.metric('예상 보유 종목', f'{funded} / 20')
            c.metric('예상 투자금', f"{result['invested_after']:,.0f}원")
            d.metric('예상 잔여 현금', f"{result['cash_after']:,.0f}원")
            st.metric('예상 회전율', f"{result['turnover']:.1f}%", help='매수·매도 제안 금액의 합계를 현재 총 평가금액으로 나눈 값입니다.')
            target_frame = pd.DataFrame(result['target_positions'])
            st.dataframe(target_frame.round(2).rename(columns=PORTFOLIO_COLUMNS), hide_index=True, width='stretch')
            if result['unfunded_targets']:
                names = ranked.set_index('code').loc[result['unfunded_targets'], 'name'].tolist()
                st.warning(f"1주 가격이 종목당 배정액보다 높아 {len(names)}종목을 편입하지 못했습니다: {', '.join(names)}")

    with orders_tab:
        if not result:
            st.info('보유 내역의 가격을 확인한 뒤 매매 제안을 계산할 수 있습니다.')
        else:
            st.caption('매도 후 매수하는 정수 수량 제안입니다. 실제 주문은 전송되지 않습니다.')
            order_frame = pd.DataFrame(result['orders'])
            if order_frame.empty:
                st.info('현재 조건에서 제안할 매매가 없습니다.')
            else:
                st.dataframe(order_frame.round(2).rename(columns=PORTFOLIO_COLUMNS), hide_index=True, width='stretch')
                st.download_button(
                    '매매 제안 CSV', order_frame.rename(columns=PORTFOLIO_COLUMNS).to_csv(index=False).encode('utf-8-sig'),
                    file_name=f'quantdesk-orders-{as_of.isoformat()}.csv', mime='text/csv', icon=':material/download:',
                )
            if result['skipped']:
                st.warning(f"{len(result['skipped'])}건은 최소 거래금액 미만이거나 현금이 부족해 제외됐습니다.")
                st.dataframe(pd.DataFrame(result['skipped']).rename(columns=PORTFOLIO_COLUMNS), hide_index=True, width='stretch')
