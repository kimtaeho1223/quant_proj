"""Streamlit audit workflow for official security lifecycle evidence."""

from datetime import date
from pathlib import Path

import streamlit as st

from quantdesk.security_lifecycle_ingestion import SecurityLifecycleIngestion
from quantdesk.security_lifecycle_source import (
    KrxLifecycleSource,
    LifecycleRawArchive,
    OfficialLifecycleCsvSource,
)
from quantdesk.security_lifecycle_store import SecurityLifecycleStore


SOURCE_LABELS = {
    'security_master': '종목 마스터',
    'new_listings': '신규 상장',
    'delistings': '상장 폐지',
    'identifier_changes': '식별자 변경',
}


def _ingestion(db_path, raw_root):
    return SecurityLifecycleIngestion(
        SecurityLifecycleStore(db_path), LifecycleRawArchive(raw_root),
    )


def import_lifecycle_csv(db_path, raw_root, dataset, content,
                         scope_start=None, scope_end=None):
    source = OfficialLifecycleCsvSource().from_bytes(
        dataset, content, scope_start=scope_start, scope_end=scope_end,
    )
    return _ingestion(db_path, raw_root).ingest(source)


def _source_state(store, dataset):
    versions = store.raw_versions(dataset)
    if versions.empty:
        return '없음'
    selected = store.source_selection().get(dataset)
    return '선택됨' if selected == int(versions.id.max()) else '검토 필요'


def render_security_lifecycle(db_path, raw_root):
    st.divider()
    st.subheader('종목 생애주기')
    st.caption('공식 KRX 원본을 보존하고 시점별 상장 종목군을 재구성합니다.')
    store = SecurityLifecycleStore(db_path)
    ingestion = SecurityLifecycleIngestion(store, LifecycleRawArchive(raw_root))

    for column, (dataset, label) in zip(st.columns(4), SOURCE_LABELS.items()):
        column.metric(label, _source_state(store, dataset))

    selected = store.source_selection()
    for dataset, label in SOURCE_LABELS.items():
        versions = store.raw_versions(dataset)
        with st.expander(label):
            actions = st.columns(2)
            if actions[0].button(
                '공식 원본 자동 수집', icon=':material/cloud_download:',
                key=f'lifecycle_fetch_{dataset}',
            ):
                try:
                    result = ingestion.ingest(KrxLifecycleSource().fetch(dataset))
                    st.success(f"{label} {result['row_count']}행을 보존했습니다.")
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
            uploaded = actions[1].file_uploader(
                '공식 CSV', type=['csv'], key=f'lifecycle_upload_{dataset}',
            )
            if st.button(
                'CSV 검증 및 보존', icon=':material/upload_file:',
                key=f'lifecycle_import_{dataset}', disabled=uploaded is None,
            ):
                try:
                    result = import_lifecycle_csv(
                        db_path, raw_root, dataset, uploaded.getvalue(),
                    )
                    st.success(f"{label} {result['row_count']}행을 보존했습니다.")
                except (ValueError, OSError) as exc:
                    st.error(str(exc))

            versions = store.raw_versions(dataset)
            if not versions.empty:
                st.dataframe(
                    versions[['id', 'scope_start', 'scope_end', 'sha256', 'fetched_at']]
                    .rename(columns={'id': '원본 버전'}),
                    hide_index=True,
                )
                latest = int(versions.id.max())
                if selected.get(dataset) != latest:
                    choices = versions.id.astype(int).tolist()
                    raw_id = st.selectbox(
                        '채택할 원본 버전', choices, index=len(choices) - 1,
                        key=f'lifecycle_version_{dataset}',
                    )
                    evidence = st.text_input(
                        '원본 선택 근거',
                        placeholder='예: KRX 공식 정정 공지와 체크섬 확인',
                        key=f'lifecycle_evidence_{dataset}',
                    )
                    if st.button(
                        '검토 원본 채택', icon=':material/fact_check:',
                        key=f'lifecycle_select_{dataset}', disabled=not evidence.strip(),
                    ):
                        try:
                            ingestion.select_raw_version(dataset, raw_id, evidence)
                            st.success(f'{label} 원본 버전을 채택했습니다.')
                        except ValueError as exc:
                            st.error(str(exc))

    versions = store.raw_versions()
    latest = versions.groupby('dataset').id.max().to_dict() if not versions.empty else {}
    current = store.source_selection()
    sources_ready = (
        set(current) == set(SOURCE_LABELS)
        and all(current.get(dataset) == int(latest.get(dataset, -1)) for dataset in SOURCE_LABELS)
    )
    if st.button(
        '생애주기 모델 검증 및 승격', icon=':material/published_with_changes:',
        key='lifecycle_promote', type='primary', disabled=not sources_ready,
    ):
        try:
            result = ingestion.build_and_promote()
            st.success(f"생애주기 모델 #{result['revision']}을 승격했습니다.")
        except (ValueError, RuntimeError, OSError) as exc:
            st.error(str(exc))

    readiness = store.readiness('1900-01-01', date.today().isoformat())
    lock_columns = st.columns(4)
    lock_columns[0].metric(
        '생애주기 준비', '완료' if readiness['lifecycle_ready'] else '잠김',
    )
    lock_columns[1].metric('투자 가능성 준비', '잠김')
    lock_columns[2].metric('기업행사 준비', '잠김')
    lock_columns[3].metric('공식 백테스트 준비', '잠김')
    for reason in readiness['blocking_reasons']:
        st.warning(reason)

    rebuilds = store.rebuilds()
    if not rebuilds.empty and rebuilds.iloc[-1].status == 'failed':
        st.error(rebuilds.iloc[-1].message)
    if readiness['fingerprint']:
        if st.button(
            '선택 원본 재빌드 검증', icon=':material/restart_alt:',
            key='lifecycle_rebuild',
        ):
            result = ingestion.rebuild()
            if result['status'] == 'passed':
                st.success('재빌드 지문이 현재 모델과 일치합니다.')
            else:
                st.error(result['message'])

    catalog = store.security_catalog()
    if not catalog.empty:
        options = catalog.security_id.tolist()
        labels = {
            row.security_id: f'{row.name} ({row.short_code})'
            for row in catalog.itertuples(index=False)
        }
        security_id = st.selectbox(
            '종목 생애주기', options, format_func=labels.get, key='lifecycle_security',
        )
        timeline = store.lifecycle_timeline(security_id)
        st.caption('사건')
        st.dataframe(timeline['events'], hide_index=True)
        st.caption('식별자·명칭·시장 유효구간')
        st.dataframe(timeline['intervals'], hide_index=True)
        evidence_ids = {
            item['raw_version_id']
            for group in timeline.values() for item in group
            if item.get('raw_version_id') is not None
        }
        evidence = store.raw_versions()
        evidence = evidence[evidence.id.isin(evidence_ids)] if not evidence.empty else evidence
        st.caption('연결된 공식 원본')
        st.dataframe(evidence[['id', 'dataset', 'sha256', 'path']], hide_index=True)
