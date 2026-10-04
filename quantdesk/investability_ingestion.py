"""Official investability collection, revision selection, and rebuilds."""

import gzip
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantdesk.investability import (
    INVESTABILITY_V1,
    build_status_model,
    investability_fingerprint,
    reconcile_investability,
)
from quantdesk.investability_source import (
    KRX_STATUS_ENDPOINT,
    InvestabilitySourceError,
    archive_investability_payload,
    decode_investability_csv,
    fetch_investability_snapshot,
    normalize_investability_rows,
)


def _observed_at():
    return datetime.now(timezone.utc).isoformat()


def _selected_ids(store):
    return {
        coverage_key: values['raw_version_id']
        for coverage_key, values in store.source_selections().items()
    }


def _read_normalized(raw):
    path = Path(raw['path'])
    with gzip.open(path, 'rb') as source:
        payload = source.read()
    return normalize_investability_rows(
        decode_investability_csv(payload),
        raw['dataset'],
        raw['requested_date'],
        raw['id'],
        raw['observed_at'],
    )


def _build_selected(store, lifecycle_reader, sessions, selections=None):
    selected = selections if selections is not None else _selected_ids(store)
    if not selected:
        raise ValueError('선택된 투자 가능성 공식 원본이 없습니다.')
    frames = []
    for coverage_key, raw_version_id in sorted(selected.items()):
        raw = store.raw_version(raw_version_id)
        if raw is None:
            raise ValueError(f'선택된 원본 버전이 없습니다: {raw_version_id}')
        if raw['requested_date'] != coverage_key:
            raise ValueError('선택된 원본의 기준일이 범위와 다릅니다.')
        frames.append(_read_normalized(raw))
    model = build_status_model(
        pd.concat(frames, ignore_index=True), lifecycle_reader, sessions,
    )
    blocking = [item for item in model.findings if item.severity == 'blocking']
    if blocking:
        raise ValueError(f'차단 상태 검증 항목이 {len(blocking)}건 있습니다.')
    return model, selected


def _archive_and_validate(store, archive_root, requested_date, payload, source_url):
    observed_at = _observed_at()
    artifact = archive_investability_payload(
        Path(archive_root), 'daily_status', requested_date, payload,
        observed_at, source_url,
    )
    raw_version_id = store.save_raw_version(artifact, pd.DataFrame())
    rows = normalize_investability_rows(
        decode_investability_csv(payload),
        artifact.dataset,
        artifact.requested_date,
        raw_version_id,
        artifact.observed_at,
    )
    store.save_source_rows(raw_version_id, rows)
    return artifact, raw_version_id


def _ingest_payload(store, archive_root, requested_date, payload,
                    lifecycle_reader, sessions, source, source_url):
    try:
        artifact, raw_version_id = _archive_and_validate(
            store, archive_root, requested_date, payload, source_url,
        )
        selected = store.source_selections().get(artifact.requested_date)
        if selected and selected['raw_version_id'] != raw_version_id:
            return {
                'status': 'revision_pending',
                'source': source,
                'raw_version_id': raw_version_id,
                'message': '같은 기준일의 공식 수정본을 확인했습니다. 선택 사유가 필요합니다.',
            }
        selection_ids = _selected_ids(store)
        selection_ids[artifact.requested_date] = raw_version_id
        model, selection_ids = _build_selected(
            store, lifecycle_reader, sessions, selection_ids,
        )
        fingerprint = store.promote(model, INVESTABILITY_V1, selection_ids)
        if selected is None:
            store.set_source_selection(
                artifact.requested_date, raw_version_id, '최초 공식 원본 자동 선택',
            )
        return {
            'status': 'promoted',
            'source': source,
            'raw_version_id': raw_version_id,
            'fingerprint': fingerprint,
            'message': '공식 투자 가능성 상태를 승격했습니다.',
        }
    except (InvestabilitySourceError, ValueError, OSError) as exc:
        return {
            'status': 'failed',
            'source': source,
            'message': str(exc),
        }


def collect_investability_date(store, archive_root: Path, requested_date: str,
                               lifecycle_reader, sessions, http_get,
                               service_key: str | None = None) -> dict:
    try:
        payload = fetch_investability_snapshot(
            requested_date, http_get, service_key,
        )
    except InvestabilitySourceError as exc:
        message = str(exc)
        return {
            'status': 'action_required' if '공식 CSV' in message else 'failed',
            'source': 'automatic',
            'message': message,
        }
    return _ingest_payload(
        store, archive_root, requested_date, payload,
        lifecycle_reader, sessions, 'automatic', KRX_STATUS_ENDPOINT,
    )


def ingest_investability_csv(store, archive_root: Path, requested_date: str,
                             payload: bytes, lifecycle_reader,
                             sessions) -> dict:
    return _ingest_payload(
        store, archive_root, requested_date, payload,
        lifecycle_reader, sessions, 'official_csv', 'manual://official-csv',
    )


def select_investability_source(store, coverage_key: str, raw_version_id: int,
                                reason: str, lifecycle_reader,
                                sessions) -> dict:
    reason = str(reason).strip()
    if not reason:
        raise ValueError('공식 수정본 선택 사유가 필요합니다.')
    selections = _selected_ids(store)
    selections[pd.Timestamp(coverage_key).date().isoformat()] = int(raw_version_id)
    model, selections = _build_selected(
        store, lifecycle_reader, sessions, selections,
    )
    fingerprint = store.promote(model, INVESTABILITY_V1, selections)
    store.set_source_selection(coverage_key, raw_version_id, reason)
    return {
        'status': 'promoted',
        'raw_version_id': int(raw_version_id),
        'fingerprint': fingerprint,
    }


def rebuild_investability_model(store, lifecycle_reader,
                                sessions) -> dict:
    expected = store.current_fingerprint()
    try:
        model, _ = _build_selected(store, lifecycle_reader, sessions)
        actual = investability_fingerprint(model, INVESTABILITY_V1)
        matched = expected == actual
        status = 'verified' if matched else 'mismatch'
        store.record_rebuild(
            expected or '', actual, 'verified' if matched else 'failed',
            '재빌드 지문 일치' if matched else '재빌드 지문 불일치',
        )
        return {
            'status': status,
            'expected_fingerprint': expected,
            'actual_fingerprint': actual,
        }
    except (InvestabilitySourceError, ValueError, OSError) as exc:
        store.record_rebuild(expected or '', '', 'failed', str(exc))
        return {
            'status': 'failed',
            'expected_fingerprint': expected,
            'actual_fingerprint': None,
            'message': str(exc),
        }


def reconcile_investability_range(store, lifecycle_reader, daily_market,
                                  start_date, end_date,
                                  lifecycle_fingerprint,
                                  market_raw_version,
                                  policy=INVESTABILITY_V1):
    model = store._model()
    results, findings = reconcile_investability(
        model, lifecycle_reader, daily_market, start_date, end_date, policy,
    )
    run_id = store.record_reconciliation(
        start_date, end_date, results, findings,
        source_model_fingerprint=store.current_fingerprint(),
        lifecycle_fingerprint=lifecycle_fingerprint,
        market_raw_version=market_raw_version,
        policy_fingerprint=policy.fingerprint,
    )
    return {'run_id': run_id, 'results': results, 'findings': findings}
