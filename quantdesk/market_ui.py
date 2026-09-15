from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import streamlit as st
from quantdesk.market import MarketStore, refresh_listing, refresh_prices, refresh_top_prices


def render_market(path):
    store = MarketStore(path)
    st.subheader('실제 주가 데이터')
    st.caption('NAVER 일별 주가 · FinanceDataReader 종목 목록 · 로컬 저장')
    st.info('재무정보 연결 전입니다. 이 데이터는 아직 샘플 전략의 순위나 매매 제안에 사용되지 않습니다.')
    listing, fetched = store.listing()
    if st.button('종목 목록 갱신', icon=':material/refresh:', key='refresh_listing'):
        with st.spinner('종목 목록을 가져오는 중입니다.'):
            success = refresh_listing(store)
        if success:
            st.success('종목 목록을 저장했습니다.')
        else:
            st.error('목록 갱신에 실패했습니다. 아래 수집 기록을 확인해 주세요. 기존 목록은 유지됩니다.')
        listing, fetched = store.listing()
    st.caption(f'목록 마지막 수집: {fetched or "아직 없음"}')
    if not listing.empty:
        st.write(f'KOSPI·KOSDAQ {len(listing):,}종목')
        with st.expander('종목 목록 · 시가총액 · 거래대금'):
            st.caption('시가총액과 거래대금은 제공처 목록의 스냅샷입니다. 수집 시각이 거래 기준일을 의미하지 않습니다.')
            st.dataframe(listing[['Code', 'Name', 'Market', 'Marcap', 'Amount']].rename(columns={
                'Code':'종목코드', 'Name':'종목명', 'Market':'시장', 'Marcap':'시가총액 (원)', 'Amount':'거래대금 (원)'}), hide_index=True)
        names = listing.set_index('Code').Name.to_dict()
        options = listing.sort_values('Marcap', ascending=False).Code.tolist()
        codes = st.multiselect('수집할 종목', options, default=[c for c in ['005930', '000660', '035420'] if c in options],
                               format_func=lambda code: f'{names[code]} ({code})', max_selections=50)
    else:
        text = st.text_input('수집할 종목코드 (쉼표로 구분)', value='005930,000660,035420')
        codes = list(dict.fromkeys(c.strip() for c in text.split(',') if c.strip()))
        st.caption('목록이 없어도 알고 있는 종목코드로 일별 주가를 수집할 수 있습니다.')
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    end = today - timedelta(days=1)
    left, right = st.columns(2)
    first = left.date_input('시작일', value=end - timedelta(days=400), max_value=end)
    last = right.date_input('종료일', value=end, max_value=end)
    st.caption('장중 미완성 데이터를 피하기 위해 어제까지만 요청합니다. 휴장일은 실제 반환된 마지막 거래일로 확인하세요.')
    if st.button('선택 종목 주가 갱신', icon=':material/download:', type='primary', key='refresh_prices'):
        if not codes or len(codes) > 50 or any(len(c) != 6 or not c.isalnum() or not c.isascii() for c in codes):
            st.error('6자리 종목코드를 1~50개 선택해 주세요.')
        elif first > last:
            st.error('시작일은 종료일보다 늦을 수 없습니다.')
        else:
            progress = st.progress(0)
            results = refresh_prices(store, codes, first.isoformat(), last.isoformat(), progress=progress.progress)
            successes = sum(r['status'] == '성공' for r in results)
            message = f'수집 완료: 성공 {successes}개, 실패 {len(results)-successes}개'
            (st.success if successes == len(results) else st.warning)(message)
    if not listing.empty:
        st.divider()
        st.caption('상위 100개 보통주 후보를 선택 목록과 관계없이 수집합니다. 제공처 응답에 따라 몇 분 걸릴 수 있습니다.')
        if st.button('상위 100개 주가 일괄 수집', icon=':material/download:', key='refresh_top_prices'):
            progress = st.progress(0)
            with st.spinner('상위 100개 후보의 일별 주가를 수집하는 중입니다.'):
                results = refresh_top_prices(store, listing, first.isoformat(), last.isoformat(), progress=progress.progress)
            successes = sum(r['status'] == '성공' for r in results)
            message = f'상위 후보 수집 완료: 성공 {successes}개, 실패 {len(results)-successes}개'
            (st.success if successes == len(results) else st.warning)(message)
    st.subheader('최근 갱신 결과')
    latest = store.latest_results()
    if latest.empty:
        st.info('아직 갱신 기록이 없습니다.')
    else:
        st.caption('종목별 마지막 시도 기준 · 종목 목록 갱신 포함')
        failures = int((latest['상태'] == '실패').sum())
        if failures:
            st.warning(f'마지막 갱신이 실패한 항목 {failures}개')
        else:
            st.success('모든 항목의 마지막 갱신이 성공했습니다.')
        st.dataframe(latest, hide_index=True)
    with st.expander('과거 수집 기록 · 최근 500건', expanded=False):
        logs = store.logs()
        if logs.empty:
            st.info('아직 수집 기록이 없습니다.')
        else:
            st.dataframe(logs, hide_index=True)
    summary = store.summary()
    st.subheader('저장된 일별 주가')
    if summary.empty:
        st.info('저장된 주가가 없습니다.')
    else:
        st.dataframe(summary.rename(columns={'Code':'종목코드'}), hide_index=True)
        selected = st.selectbox('조회할 종목', summary.Code.tolist())
        data = store.prices(selected)
        st.line_chart(data.set_index('Date')['Close'], color='#198573')
        st.caption('제공처 종가 기준입니다. 수정주가와 기업행사 반영은 백테스트 연결 전에 추가 검증합니다. 일별 거래대금은 이 자료에 포함되지 않습니다.')
        st.dataframe(data.rename(columns={'Date':'거래일', 'Open':'시가', 'High':'고가', 'Low':'저가', 'Close':'종가', 'Volume':'거래량'}), hide_index=True)
