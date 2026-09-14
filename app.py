import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from quantdesk.core import DEFAULT_WEIGHTS, score_stocks, rebalance
from quantdesk.data import sample_stocks, SAMPLE_DATE
from quantdesk.storage import Store
from quantdesk.market_ui import render_market
from quantdesk.dart_ui import render_dart

st.set_page_config(page_title='Quant Desk KR', page_icon=':material/monitoring:', layout='wide')
page = st.sidebar.radio('화면', ['샘플 전략', '실제 주가 데이터', '재무정보'], key='page')
if page == '재무정보':
    st.title('Quant Desk KR')
    render_dart(os.environ.get('QUANTDESK_DART_DB', str(Path(__file__).parent / 'data' / 'dart.db')))
    st.stop()
if page == '실제 주가 데이터':
    st.title('Quant Desk KR')
    render_market(os.environ.get('QUANTDESK_MARKET_DB', str(Path(__file__).parent / 'data' / 'market.db')))
    st.stop()
st.markdown('''<style>
.block-container {max-width:1440px;padding-top:2rem;}
h1 {font-size:2rem !important;letter-spacing:0 !important;}
h2,h3 {letter-spacing:0 !important;}
h3 {font-size:1.2rem !important;}
[data-testid="stMetricValue"] {font-size:1.65rem;}
@media (max-width: 760px) {
  [data-testid="stMetricValue"] {font-size:1.1rem;}
}
[data-testid="stSidebar"] {background:#f2f5f4;}
button {border-radius:5px !important;}
</style>''', unsafe_allow_html=True)

store = Store(os.environ.get('QUANTDESK_DB', str(Path(__file__).parent / 'data' / 'quantdesk.db')))
stocks = sample_stocks()
holdings, cash = store.load_account()
labels = dict(momentum='모멘텀', value='가치', quality='퀄리티', volatility='저변동성')
columns = dict(code='종목코드', name='종목명', sector='업종', rank='순위', score='종합 점수',
               price='가상 주가 (원)', momentum='12개월 수익률 (%)', value='이익수익률 (%)',
               quality='ROE (%)', volatility='연 변동성 (%)', side='구분', quantity='수량',
               amount='거래금액 (원)', cost='비용 여유분 (원)', reason='사유')


def table(frame):
    st.dataframe(frame.rename(columns=columns), hide_index=True, width='stretch')


with st.sidebar:
    st.subheader('전략 설정')
    st.caption('KR Aggressive Multifactor 20')
    weights = {key: st.slider(labels[key], 0, 100, val, 5, key=f'weight_{key}') for key, val in DEFAULT_WEIGHTS.items()}
    st.metric('팩터 비중 합계', f'{sum(weights.values())}%')
    fee = st.number_input('거래비용 여유분 (%)', min_value=0.0, max_value=5.0, value=0.3, step=0.05,
                          help='매수와 매도에 동일하게 적용하는 시뮬레이션 가정입니다. 실제 세율이나 수수료가 아닙니다.') / 100
    st.divider()
    st.text('매주 점검 · 목표 20종목')
    st.text('동일 비중 · 기존 보유 30위까지 유지')
    st.caption('최소 거래금액 10만원\n\n시가총액 1,000억원 이상\n\n일평균 거래대금 10억원 이상')

st.title('Quant Desk KR')
st.caption('한국 주식 · 주간 멀티팩터 · 개인 투자 데스크')
st.warning(f'샘플 모드 | 기준일 {SAMPLE_DATE} | 종목·가격·재무지표는 모두 가상입니다. 실제 시세는 왼쪽의 실제 주가 데이터 화면에서 확인할 수 있습니다.')
try:
    ranked = score_stocks(stocks, weights)
    result = rebalance(ranked, holdings, cash, stocks.set_index('code').price.to_dict(), fee_rate=fee)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

overview, ranking, portfolio, orders, journal = st.tabs(['대시보드', '종목 순위', '내 포트폴리오', '매매 제안', '투자 기록'])
with overview:
    a, b, c, d = st.columns(4)
    a.metric('총 평가금액', f"{result['equity']/10000:,.1f}만원")
    b.metric('보유 현금', f'{cash/10000:,.1f}만원')
    c.metric('선정 종목', f"{len(result['targets'])} / 20")
    d.metric('매매 제안', f"{len(result['orders'])}건")
    st.divider()
    left, right = st.columns([2, 1])
    target_frame = ranked[ranked.code.isin(result['targets'])]
    with left:
        st.subheader('이번 주 선정 종목')
        table(target_frame[['rank', 'name', 'sector', 'score', 'price']].round(2))
    with right:
        st.subheader('선정 종목 업종 구성')
        st.bar_chart(target_frame.sector.value_counts().rename('종목 수'), color='#198573', horizontal=True)
        st.subheader('데이터 현황')
        st.write(f'전체 {len(stocks)}종목 · 조건 통과 {len(ranked)}종목')
        st.caption('수익률 성과는 실제 시계열과 백테스트 연결 후 제공됩니다.')

with ranking:
    st.subheader('팩터별 종목 순위')
    search = st.text_input('종목 검색', placeholder='종목명 또는 코드')
    sector = st.selectbox('업종', ['전체'] + sorted(stocks.sector.unique().tolist()))
    visible = ranked[ranked.name.str.contains(search, regex=False) | ranked.code.str.contains(search, regex=False)]
    if sector != '전체':
        visible = visible[visible.sector == sector]
    table(visible[['rank', 'code', 'name', 'sector', 'score', 'momentum', 'value', 'quality', 'volatility']].round(2))
    with st.expander('점수 산정 기준'):
        st.write('조건을 통과한 종목끼리 각 지표의 백분위 점수를 계산합니다. 수익률·이익수익률·ROE는 클수록, 변동성은 작을수록 높은 점수입니다. 동점은 종목코드 순으로 정렬합니다.')
        st.write('현재 모멘텀은 가상 12개월 수익률 단일 지표입니다. 실제 데이터 단계에서 기간과 공시 반영 시점을 확정합니다.')

with portfolio:
    st.subheader('보유 종목과 현금')
    with st.form('account'):
        new_cash = st.number_input('보유 현금 (원)', min_value=0, value=int(cash), step=100000)
        editor = stocks[['code', 'name', 'price']].copy()
        editor['quantity'] = editor.code.map(holdings).fillna(0).astype(int)
        edited = st.data_editor(editor.rename(columns=columns), hide_index=True, width='stretch',
            disabled=['종목코드', '종목명', '가상 주가 (원)'],
            column_config={'수량': st.column_config.NumberColumn('수량', min_value=0, step=1, required=True)}, key='holdings_editor')
        submitted = st.form_submit_button('보유 내역 저장', icon=':material/save:')
        if submitted:
            quantities = edited['수량']
            if quantities.isna().any() or (quantities < 0).any() or (quantities % 1 != 0).any():
                st.error('수량은 0 이상의 정수로 입력해 주세요.')
            else:
                store.save_account({row['종목코드']: int(row['수량']) for _, row in edited.iterrows() if row['수량'] > 0}, new_cash)
                st.session_state['account_saved'] = True
                st.rerun()
    if st.session_state.pop('account_saved', False):
        st.success('보유 내역을 저장했습니다.')

with orders:
    st.subheader('주간 매매 제안')
    st.caption('정수 주식 수량 기준 · 매도 후 매수 · 실제 주문은 전송되지 않습니다.')
    order_frame = pd.DataFrame(result['orders'])
    if order_frame.empty:
        st.info('현재 조건에서 제안할 매매가 없습니다.')
    else:
        table(order_frame.round(2))
        st.download_button('매매 제안 CSV', order_frame.rename(columns=columns).to_csv(index=False).encode('utf-8-sig'),
                           file_name='quantdesk-demo-orders.csv', mime='text/csv', icon=':material/download:')
    st.metric('매매 후 예상 현금', f"{result['cash_after']:,.0f}원")
    if result['skipped']:
        st.warning(f"{len(result['skipped'])}건은 소액 또는 현금 부족으로 제외되었습니다. 목표 비중과 실제 비중이 다를 수 있습니다.")
        table(pd.DataFrame(result['skipped']))
    with st.expander('매매 후 예상 보유 내역'):
        after = stocks[stocks.code.isin(result['holdings_after'])][['code', 'name', 'price']].copy()
        after['quantity'] = after.code.map(result['holdings_after'])
        table(after)
    note = st.text_area('투자 메모', placeholder='이번 주 판단과 확인할 사항')
    if st.button('이번 주 제안 기록', key='save_decision', icon=':material/save:', type='primary'):
        store.save_snapshot(result, note, dict(weights=weights, fee_rate=fee, source='fictional', date=SAMPLE_DATE, holdings=holdings, cash=cash))
        st.success('매매 제안을 저장했습니다. 보유 내역은 직접 체결 내역에 맞춰 수정해 주세요.')

with journal:
    st.subheader('투자 기록')
    history = store.history()
    if not history:
        st.info('아직 저장한 매매 제안이 없습니다.')
    for entry in history:
        timestamp = datetime.fromisoformat(entry['created']).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M')
        with st.expander(f"#{entry['id']} · {timestamp} · {len(entry['result']['orders'])}건"):
            st.write(entry['note'] or '메모 없음')
            st.caption('가상 데이터 기반 제안 기록 · 체결 기록 아님')
            table(pd.DataFrame(entry['result']['orders']))
