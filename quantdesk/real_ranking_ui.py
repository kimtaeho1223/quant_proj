"""Streamlit view for the locally computed multifactor research ranking."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from quantdesk.dart import DartStore
from quantdesk.market import MarketStore
from quantdesk.real_ranking import build_ranking, candidate_universe


RANK_COLUMNS = {
    'rank': '순위', 'code': '종목코드', 'name': '종목명', 'sector': '업종', 'score': '종합 점수',
    'price': '종가 (원)', 'momentum': '12개월 수익률 (%)', 'value': '이익수익률 (%)',
    'quality': 'ROE (%)', 'volatility': '연 변동성 (%)', 'market_cap': '시가총액 (원)',
    'turnover': '거래대금 (원)', 'price_date': '주가 기준일', 'filing_date': '재무 공시일',
    'statement_basis': '재무제표 기준', 'Code': '종목코드', 'Name': '종목명', '제외 사유': '제외 사유',
}


def render_real_ranking(market_path, dart_path):
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
