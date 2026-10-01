"""Streamlit workflow for historical daily full-market collection."""

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from quantdesk.historical_market import HistoricalIngestionService, RawArchive
from quantdesk.historical_market_store import HistoricalMarketStore


def launch_historical_worker(job_id, db_path, raw_root, service_key):
    command = [
        sys.executable, '-m', 'quantdesk.historical_market_worker',
        '--job-id', str(job_id), '--db', str(db_path), '--raw-root', str(raw_root),
    ]
    environment = os.environ.copy()
    environment['QUANTDESK_DATA_GO_KR_KEY'] = service_key
    options = {
        'cwd': str(Path(__file__).resolve().parents[1]),
        'env': environment,
        'stdin': subprocess.DEVNULL,
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
        'shell': False,
    }
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session'] = True
    return subprocess.Popen(command, **options)


def render_historical_market(db_path, raw_root, launcher=launch_historical_worker):
    st.subheader('과거 시장 데이터')
    store = HistoricalMarketStore(db_path)
    api_key = st.text_input(
        '공공데이터포털 인증키', type='password', key='historical_api_key',
    )
    yesterday = date.today() - timedelta(days=1)
    columns = st.columns(2)
    start = columns[0].date_input(
        '수집 시작일', value=yesterday - timedelta(days=1_500), max_value=yesterday,
        key='historical_start_date',
    )
    end = columns[1].date_input(
        '수집 종료일', value=yesterday, max_value=yesterday,
        key='historical_end_date',
    )
    invalid_range = start > end
    if invalid_range:
        st.error('수집 시작일은 종료일보다 늦을 수 없습니다.')

    if st.button(
        '전체시장 수집 시작', icon=':material/download:', type='primary',
        key='historical_start', disabled=not api_key or invalid_range,
    ):
        service = HistoricalIngestionService(store, RawArchive(raw_root), source=None)
        job_id = service.create_job(start.isoformat(), end.isoformat())
        launcher(job_id, db_path, raw_root, api_key)
        st.success(f'수집 작업 #{job_id}을 시작했습니다.')

    latest = store.latest_job()
    if latest is None:
        st.info('아직 과거 전체시장 수집 작업이 없습니다.')
        st.warning('공식 백테스트 사용 불가: 과거 시장 자료 수집이 필요합니다.')
        return

    st.divider()
    st.write(f"수집 작업 #{latest['id']} · {latest['start_date']} ~ {latest['end_date']}")
    summary = store.job_summary(latest['id'])
    remaining = summary['pending'] + summary['failed'] + summary['calendar_unresolved'] + summary['quarantined']
    labels = [
        ('전체 날짜', summary['total']),
        ('승격', summary['promoted']),
        ('휴장 확인', summary['non_session']),
        ('격리', summary['quarantined']),
        ('실패', summary['failed']),
        ('남은 날짜', remaining),
    ]
    for column, (label, value) in zip(st.columns(6), labels):
        column.metric(label, value)

    controls = st.columns(3)
    if controls[0].button('일시중지', icon=':material/pause:', key='historical_pause',
                          disabled=latest['pause_requested']):
        store.set_pause(latest['id'], True)
        st.info('현재 날짜 처리가 끝난 뒤 일시중지합니다.')
    if controls[1].button('재개', icon=':material/play_arrow:', key='historical_resume',
                          disabled=not latest['pause_requested'] or not api_key):
        store.set_pause(latest['id'], False)
        launcher(latest['id'], db_path, raw_root, api_key)
        st.success('수집 작업을 재개했습니다.')
    controls[2].button('상태 새로고침', icon=':material/refresh:', key='historical_refresh')

    dates = store.job_dates(latest['id'])
    if not dates.empty:
        problem = dates[dates.state.isin(['failed', 'quarantined', 'calendar_unresolved'])]
        if not problem.empty:
            st.dataframe(problem.rename(columns={
                'trade_date': '날짜', 'state': '상태', 'attempts': '시도', 'message': '내용',
            })[['날짜', '상태', '시도', '내용']], hide_index=True)
            selected = st.selectbox('CSV로 보충할 날짜', problem.trade_date.tolist(), key='historical_csv_date')
            uploaded = st.file_uploader('공식 CSV', type=['csv'], key='historical_csv_file')
            if st.button('공식 CSV 검증 및 반영', key='historical_csv_import', disabled=uploaded is None):
                service = HistoricalIngestionService(store, RawArchive(raw_root), source=None)
                try:
                    result = service.import_csv(latest['id'], selected, uploaded.getvalue())
                    st.success(f"{selected} CSV 처리 완료: {result['state']}")
                except (ValueError, OSError) as exc:
                    st.error(str(exc))

            selected_state = problem.set_index('trade_date').loc[selected, 'state']
            if selected_state == 'calendar_unresolved':
                evidence = st.text_input(
                    '공식 휴장 근거',
                    placeholder='예: 한국거래소 2026년 휴장일 공지 확인',
                    key='historical_non_session_evidence',
                )
                if st.button(
                    '휴장일로 확정',
                    icon=':material/event_busy:',
                    key='historical_confirm_non_session',
                    disabled=not evidence.strip(),
                ):
                    service = HistoricalIngestionService(store, RawArchive(raw_root), source=None)
                    try:
                        service.confirm_non_session(latest['id'], selected, evidence)
                        st.success(f'{selected}을 공식 휴장일로 확정했습니다.')
                    except ValueError as exc:
                        st.error(str(exc))

    acquisition_complete = remaining == 0 and summary['promoted'] + summary['non_session'] == summary['total']
    if acquisition_complete:
        st.success('일별 전체시장 원본 수집이 완료되었습니다.')
    else:
        st.info('일별 전체시장 원본 수집이 아직 완료되지 않았습니다.')
    st.warning('공식 백테스트 사용 불가: 종목 생애주기와 기업행사 검증 단계가 남아 있습니다.')
