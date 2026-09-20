from datetime import datetime
import pandas as pd
import streamlit as st
from quantdesk.dart import DartClient, DartStore, DartError, refresh_top_statements
from quantdesk.market import MarketStore
from quantdesk.financials import summarize_statement, match_company
from quantdesk.real_ranking import candidate_universe
from pathlib import Path
import os


def render_dart(path):
    store = DartStore(path)
    st.subheader('재무정보 · OpenDART')
    st.caption('연간 사업보고서 · 연결·별도 구분 · 공시 접수일 확인')
    st.info('저장된 재무자료는 실제 멀티팩터 순위의 가치·퀄리티 팩터에 사용됩니다. 백테스트에는 아직 반영되지 않습니다.')
    key = st.text_input('OpenDART 인증키', type='password', key='dart_api_key',
                        help='이 브라우저 세션에서만 사용하며 파일이나 GitHub에 저장하지 않습니다.')
    st.link_button('인증키 신청 / 관리', 'https://opendart.fss.or.kr/')
    companies = store.companies()
    market_path = os.environ.get('QUANTDESK_MARKET_DB', str(Path(__file__).resolve().parents[1] / 'data' / 'market.db'))
    listing, listing_fetched = MarketStore(market_path).listing()
    if st.button('기업 목록 갱신', icon=':material/refresh:', key='dart_companies'):
        try:
            with st.spinner('기업 목록을 가져오는 중입니다.'):
                companies = DartClient(key).companies()
                store.save_companies(companies)
            st.success(f'{len(companies):,}개 기업을 저장했습니다.')
        except DartError as exc:
            st.error(str(exc))
    if companies:
        matched = [c for c in companies if not match_company(c, companies, listing)]
        include_unmatched = st.checkbox('대조되지 않은 기업도 표시', value=False)
        options = sorted(companies) if include_unmatched else sorted(matched)
        if not options:
            st.warning('대조된 기업이 없습니다. 실제 주가 데이터에서 종목 목록을 갱신하거나 대조되지 않은 기업 표시를 선택해 주세요.')
        stock = st.selectbox('기업', options, format_func=lambda c: f"{companies[c]['name']} ({c})")
        if stock and match_company(stock, companies, listing):
            st.warning(match_company(stock, companies, listing))
        year = st.number_input('사업연도', min_value=2015, max_value=datetime.now().year, value=datetime.now().year - 1, step=1)
        basis = st.radio('재무제표 기준', ['CFS', 'OFS'], format_func=lambda b: '연결' if b == 'CFS' else '별도', horizontal=True)
        if st.button('연간 재무제표 수집', icon=':material/download:', type='primary', disabled=not stock):
            try:
                with st.spinner('재무제표와 공시일을 확인하는 중입니다.'):
                    data = DartClient(key).annual(companies[stock]['corp_code'], year, basis)
                    store.save_statement(stock, year, basis, data)
                st.success(f"저장 완료 · 공시일 {data['filed_at']}")
            except DartError as exc:
                st.error(str(exc))
        candidates = candidate_universe(listing)
        coverage = store.coverage(year)
        saved_count = sum(code in coverage for code in candidates.Code)
        mode = st.radio('수집 방식', ['부족한 종목만', '전체 재확인'], horizontal=True,
                        key='financial_refresh_mode')
        pending_count = len(candidates) - saved_count if mode == '부족한 종목만' else len(candidates)
        st.divider()
        st.caption(f'실제 멀티팩터 순위와 같은 시가총액 상위 후보 {len(candidates)}개를 수집합니다. 연결 재무제표를 우선하고, 자료가 없는 기업만 별도로 대체합니다.')
        st.caption(f'저장 완료 {saved_count}개 · 수집 예정 {pending_count}개')
        if st.button('상위 100개 재무제표 일괄 수집', icon=':material/download:',
                     key='refresh_top_statements', disabled=candidates.empty):
            try:
                client = DartClient(key)
                progress = st.progress(0)
                with st.spinner('상위 후보의 연간 재무제표와 공시일을 확인하는 중입니다.'):
                    results = refresh_top_statements(store, listing, companies, year, client,
                                                     progress=progress.progress,
                                                     force=mode == '전체 재확인')
                successes = sum(result['status'] == '성공' for result in results)
                skipped = sum(result['status'] == '건너뜀' for result in results)
                failures = len(results) - successes - skipped
                message = f'일괄 수집 완료: 성공 {successes}개, 실패 {failures}개, 건너뜀 {skipped}개'
                (st.success if failures == 0 else st.warning)(message)
            except DartError as exc:
                st.error(str(exc))
    else:
        st.caption('기업 목록: 아직 없음')
    latest = store.latest_results()
    if latest:
        st.subheader('최근 일괄 수집 결과')
        st.dataframe(pd.DataFrame(latest), hide_index=True)
    st.subheader('저장된 재무제표')
    statements = store.statements()
    if statements:
        st.subheader('재무 지표 검증')
        st.caption(f'종목 목록 수집: {listing_fetched or "없음"} · 거래 기준일은 별도 확인 필요')
        metrics = pd.DataFrame([summarize_statement(e, companies, listing) for e in statements])
        st.dataframe(metrics, hide_index=True)
        with st.expander('계산 기준'):
            st.write('ROE = 연간 순이익 ÷ 당기·전기 평균 자기자본. 연결은 지배기업 소유주 귀속 이익·자본, 별도는 순이익·자본총계를 사용합니다. 표준 계정이 없거나 금액이 충돌하면 계산하지 않습니다.')
            st.write('가치 지표는 해당 연도 이익과 저장된 시가총액 스냅샷의 비율입니다. 서로 다른 사업연도·연결·별도 자료를 통합한 순위가 아니며, 아직 TTM·우선주·과거 시점 정합성을 반영하지 않은 참고값입니다.')
    if not statements:
        st.info('저장된 재무제표가 없습니다.')
    for entry in statements:
        name = companies.get(entry['stock'], {}).get('name', entry['stock'])
        with st.expander(f"{name} · {entry['year']}년 · {entry['basis']} · 공시 {entry['filed_at']}"):
            st.caption(f"수집 시각 {entry['fetched']}")
            st.link_button('DART 공시 원문', 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + entry['receipt'])
            fields = {'sj_nm':'재무제표', 'account_id':'계정 ID', 'account_nm':'계정명', 'thstrm_nm':'당기',
                      'thstrm_amount':'당기 금액', 'frmtrm_amount':'전기 금액', 'currency':'통화'}
            frame = pd.DataFrame(entry['rows'])
            st.dataframe(frame[[f for f in fields if f in frame]].rename(columns=fields), hide_index=True)
    st.caption('과거 시점 재현에는 당시 정정 전 원문과 이용 가능 시점의 추가 검증이 필요합니다.')
