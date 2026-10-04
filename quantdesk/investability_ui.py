"""Streamlit audit workflow for point-in-time investability status."""

from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from quantdesk.management_history import (
    KRX_MANAGEMENT_HISTORY_URL,
    archive_management_history,
    compare_management_dates,
    load_management_history,
    management_state_on,
)
from quantdesk.investability_ingestion import (
    collect_investability_date,
    ingest_investability_csv,
    rebuild_investability_model,
    reconcile_investability_range,
    select_investability_source,
)
from quantdesk.investability_source import MAX_RAW_BYTES


def uploaded_csv_bytes(uploaded):
    payload = bytes(uploaded.getvalue())
    if not payload:
        raise ValueError('업로드한 공식 CSV가 비어 있습니다.')
    if len(payload) > MAX_RAW_BYTES:
        raise ValueError('업로드한 공식 CSV가 허용 크기를 초과했습니다.')
    return payload


def _session_dates(sessions):
    if not isinstance(sessions, pd.DataFrame) or sessions.empty:
        return []
    column = 'date' if 'date' in sessions else 'Date'
    return sorted(pd.to_datetime(sessions[column]).dt.date.astype(str).unique())


def _daily_market(daily_market_reader, dates):
    frames = []
    raw_ids = set()
    for day in dates:
        frame = daily_market_reader.canonical_day(day)
        if frame is None or frame.empty:
            continue
        data = frame.copy()
        if 'trade_date' not in data:
            data['trade_date'] = day
        frames.append(data)
        if 'raw_version_id' in data:
            raw_ids.update(str(int(value)) for value in data.raw_version_id.dropna().unique())
    columns = ['trade_date', 'code', 'name', 'market', 'close', 'volume', 'raw_version_id']
    market = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)
    return market, ','.join(sorted(raw_ids)) or 'none'


def _readiness_labels(store, lifecycle_reader, start, end):
    lifecycle = (
        lifecycle_reader.readiness(start, end)
        if hasattr(lifecycle_reader, 'readiness')
        else {'lifecycle_ready': False, 'blocking_reasons': ['생애주기 준비 상태를 확인할 수 없습니다.']}
    )
    investability = store.readiness(start, end, 'investability-v1')
    return lifecycle, investability, {
        '종목 생애주기': bool(lifecycle.get('lifecycle_ready', False)),
        '투자 가능성': bool(investability.get('investability_ready', False)),
        '기업행사': False,
        '공식 백테스트': False,
    }


def render_management_history_audit(archive_root: Path) -> None:
    st.subheader('관리종목 지정·해제 내역 대조')
    st.caption('KRX 개별종목 변경 내역은 전체시장 일별 상태 자료가 아닙니다. 백테스트 입력으로 승격되지 않습니다.')
    st.link_button('KRX 원본 화면', KRX_MANAGEMENT_HISTORY_URL)
    uploaded = st.file_uploader(
        'KRX 관리종목 변경 내역 CSV', type=['csv'],
        key='management_history_file',
    )
    if st.button(
        '변경 내역 원본 보존', key='management_history_import',
        disabled=uploaded is None,
    ):
        try:
            artifact = archive_management_history(
                Path(archive_root), uploaded_csv_bytes(uploaded),
            )
            st.success(f'원본을 보존했습니다. SHA-256: {artifact.sha256}')
        except (ValueError, OSError) as exc:
            st.error(str(exc))

    files = sorted((Path(archive_root) / 'management_history').glob(
        '*/*.management_history.csv.gz'
    ))
    if not files:
        return
    selected = st.selectbox(
        '보존된 변경 내역', files,
        format_func=lambda path: f'{path.parent.name} · {path.name.split("-")[1][:12]}',
        key='management_history_archive',
    )
    try:
        history = load_management_history(selected)
    except (ValueError, OSError, EOFError) as exc:
        st.error(f'보존 원본을 읽을 수 없습니다: {exc}')
        return
    st.caption(f'{history.name.iloc[0]} ({history.short_code.iloc[0]}) · {history.date.min()} ~ {history.date.max()}')
    events = history[history.event.ne('-')]
    st.dataframe(
        events[['date', 'event', 'reason', 'source_row_number']],
        hide_index=True, width='stretch',
    )
    boundary_indices = sorted({
        index for event_index in events.index for index in (event_index - 1, event_index)
        if index >= 0
    })
    boundaries = history.loc[boundary_indices, ['date', 'event']].copy()
    boundaries['derived_state'] = boundaries.date.map(
        lambda day: management_state_on(history, day)
    )
    st.dataframe(boundaries, hide_index=True, width='stretch')
    st.info('공시 시각이 없는 변경 내역입니다. 효력 날짜와 당시 인지 가능 시각은 별도로 검증해야 합니다.')

    columns = st.columns(2)
    designated = columns[0].date_input(
        '공시 지정일', value=None, key=f'management_designated_{selected.name}',
    )
    released = columns[1].date_input(
        '공시 해제일', value=None, key=f'management_released_{selected.name}',
    )
    if st.button(
        '공시 날짜 대조', key='management_compare',
        disabled=designated is None or released is None,
    ):
        result = compare_management_dates(
            history, designated.isoformat(), released.isoformat(),
        )
        if result['matched']:
            st.success('공시 날짜와 KRX 변경 내역 날짜가 일치합니다. 공개 시각은 미검증입니다.')
        else:
            st.error('공시 날짜와 KRX 변경 내역 날짜가 일치하지 않습니다.')


def render_investability_audit(store, archive_root: Path,
                               lifecycle_reader, sessions,
                               daily_market_reader) -> None:
    st.subheader('투자 가능성 상태 감사')
    dates = _session_dates(sessions)
    versions = store.raw_versions()
    latest_source_day = (
        versions.requested_date.max() if not versions.empty else None
    )
    default_day = pd.Timestamp(
        latest_source_day or (dates[-1] if dates else pd.Timestamp.today())
    ).date()
    selected_day = st.date_input(
        '공식 상태 기준일', value=default_day, key='investability_date',
    ).isoformat()

    controls = st.columns(2)
    if controls[0].button(
        '공식 상태 자동 수집', icon=':material/download:',
        key='investability_collect',
    ):
        result = collect_investability_date(
            store, Path(archive_root), selected_day,
            lifecycle_reader, sessions, requests.get,
        )
        if result['status'] == 'promoted':
            st.success(result['message'])
        elif result['status'] == 'action_required':
            st.warning(result['message'])
        else:
            st.error(result['message'])

    uploaded = controls[1].file_uploader(
        'KRX 공식 CSV', type=['csv'], key='investability_csv_file',
    )
    if st.button(
        '공식 CSV 검증 및 반영', key='investability_csv_import',
        disabled=uploaded is None,
    ):
        try:
            result = ingest_investability_csv(
                store, Path(archive_root), selected_day,
                uploaded_csv_bytes(uploaded), lifecycle_reader, sessions,
            )
            if result['status'] == 'promoted':
                st.success(result['message'])
            elif result['status'] == 'revision_pending':
                st.warning(result['message'])
            else:
                st.error(result['message'])
        except (ValueError, OSError) as exc:
            st.error(str(exc))

    selections = store.source_selections()
    pending = pd.DataFrame()
    if not versions.empty:
        latest = versions.loc[
            versions.groupby('requested_date')['id'].idxmax()
        ]
        pending = latest[
            latest.apply(
                lambda row: selections.get(row.requested_date, {}).get('raw_version_id') != row.id,
                axis=1,
            )
        ].copy()
    if not pending.empty:
        st.warning(f'선택 사유가 필요한 공식 수정본이 {len(pending)}개 있습니다.')
        raw_id = st.selectbox(
            '검토할 원본 버전', pending.id.tolist(),
            format_func=lambda value: f'원본 #{value}',
            key='investability_revision_id',
        )
        reason = st.text_input(
            '공식 수정본 선택 사유', key='investability_revision_reason',
        )
        if st.button(
            '수정본 선택 및 승격', key='investability_select_revision',
            disabled=not reason.strip(),
        ):
            try:
                row = pending[pending.id.eq(raw_id)].iloc[0]
                select_investability_source(
                    store, row.requested_date, int(raw_id), reason,
                    lifecycle_reader, sessions,
                )
                st.success('선택 사유와 함께 공식 수정본을 승격했습니다.')
            except (ValueError, OSError) as exc:
                st.error(str(exc))

    model_available = store.current_fingerprint() is not None
    actions = st.columns(2)
    if actions[0].button(
        '선택 원본 재빌드 검증', icon=':material/replay:',
        key='investability_rebuild', disabled=not selections,
    ):
        rebuilt = rebuild_investability_model(store, lifecycle_reader, sessions)
        if rebuilt['status'] == 'verified':
            st.success('선택 원본 재빌드 지문이 일치합니다.')
        else:
            st.error('선택 원본 재빌드 지문이 일치하지 않습니다.')
    if actions[1].button(
        '생애주기·시장 대조', icon=':material/fact_check:',
        key='investability_reconcile', disabled=not model_available,
    ):
        start = dates[0] if dates else selected_day
        end = dates[-1] if dates else selected_day
        market, market_versions = _daily_market(daily_market_reader, dates)
        lifecycle_fingerprint = (
            lifecycle_reader.current_fingerprint()
            if hasattr(lifecycle_reader, 'current_fingerprint') else 'unknown'
        )
        result = reconcile_investability_range(
            store, lifecycle_reader, market, start, end,
            lifecycle_fingerprint, market_versions,
        )
        if any(item.severity in {'blocking', 'quarantine'} for item in result['findings']):
            st.warning('대조 결과에 차단 또는 격리 항목이 있습니다.')
        else:
            st.success('생애주기·시장 대조를 기록했습니다.')

    start = dates[0] if dates else selected_day
    end = dates[-1] if dates else selected_day
    lifecycle_status, investability_status, labels = _readiness_labels(
        store, lifecycle_reader, start, end,
    )
    for column, (label, ready) in zip(st.columns(4), labels.items()):
        column.metric(label, '준비' if ready else '잠금')

    if investability_status.get('unknown_dates'):
        st.warning(
            '공식 상태 미확인 날짜: '
            + ', '.join(investability_status['unknown_dates'])
        )
    if lifecycle_status.get('blocking_reasons'):
        st.info(' '.join(lifecycle_status['blocking_reasons']))
    if investability_status.get('blocking_reasons'):
        st.info(' '.join(investability_status['blocking_reasons']))
    st.warning('기업행사와 공식 백테스트 준비 상태는 이 단계에서 계속 잠겨 있습니다.')

    latest_rebuild = store.latest_rebuild()
    if latest_rebuild and latest_rebuild['status'] == 'failed':
        st.error(f"최근 재빌드 검증 실패: {latest_rebuild['message']}")

    if not versions.empty:
        st.caption('보존된 공식 원본 버전')
        st.dataframe(versions, hide_index=True, width='stretch')
    findings = store.findings()
    if not findings.empty:
        st.caption('검증 및 대조 항목')
        st.dataframe(findings, hide_index=True, width='stretch')

    listed = store.listed_securities(selected_day)
    if not listed.empty:
        decisions = []
        for row in listed.itertuples(index=False):
            decision = store.decision(
                row.security_id, f'{selected_day}T23:59:59+09:00',
                'investability-v1',
            )
            decisions.append({
                'security_id': row.security_id,
                'short_code': row.short_code,
                'name': row.name,
                'state': decision['state'],
                'reason_codes': ','.join(decision['reason_codes']),
                'evidence_ids': ','.join(decision['evidence_ids']),
            })
        st.caption('종목별 판정과 evidence')
        st.dataframe(pd.DataFrame(decisions), hide_index=True, width='stretch')
    render_management_history_audit(archive_root)
