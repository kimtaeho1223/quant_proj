"""Auditable ingestion and deterministic rebuild workflow for lifecycle evidence."""

import gzip
import json

import pandas as pd

from quantdesk.security_lifecycle import (
    build_lifecycle_model,
    lifecycle_fingerprint,
    normalize_lifecycle_rows,
)
from quantdesk.security_lifecycle_source import (
    LifecycleSourceResult,
    SUPPORTED_DATASETS,
    decode_lifecycle_source,
)


class SecurityLifecycleIngestion:
    PARSER_VERSION = 'lifecycle-csv-v1'
    RULES_VERSION = 'lifecycle-rules-v1'

    def __init__(self, store, archive):
        self.store = store
        self.archive = archive

    def ingest(self, result):
        archived = self.archive.store(result)
        raw_version_id = self.store.record_raw_version(
            archived, result.safe_metadata, self.PARSER_VERSION,
        )
        manifest = self.store.raw_versions()
        record = manifest.loc[manifest.id.eq(raw_version_id)].iloc[0]
        frame = decode_lifecycle_source(result)
        rows = normalize_lifecycle_rows(
            frame, result.dataset, raw_version_id, record.fetched_at,
        )
        self.store.save_source_rows(raw_version_id, rows)

        selected = self.store.source_selection()
        if result.dataset not in selected:
            self.store.select_raw_version(
                result.dataset, raw_version_id, '최초 구조 검증 통과 원본 자동 선택',
            )
        selected_id = self.store.source_selection().get(result.dataset)
        return {
            'dataset': result.dataset,
            'raw_version_id': raw_version_id,
            'sha256': archived.sha256,
            'archive_path': str(archived.path),
            'row_count': len(rows),
            'selected': selected_id == raw_version_id,
        }

    def select_raw_version(self, dataset, raw_version_id, evidence):
        self.store.select_raw_version(dataset, raw_version_id, evidence)

    def _require_current_selections(self):
        selected = self.store.source_selection()
        missing = sorted(set(SUPPORTED_DATASETS) - set(selected))
        if missing:
            raise ValueError(f'선택되지 않은 공식 자료: {", ".join(missing)}')
        versions = self.store.raw_versions()
        latest = versions.groupby('dataset').id.max().to_dict()
        outdated = [
            dataset for dataset, raw_id in latest.items()
            if selected.get(dataset) != int(raw_id)
        ]
        if outdated:
            raise ValueError(
                f'검토되지 않은 원본 버전이 있습니다: {", ".join(sorted(outdated))}'
            )
        return selected

    def _selected_model(self):
        selected = self._require_current_selections()
        versions = self.store.raw_versions().set_index('id')
        frames = []
        for dataset in sorted(selected):
            raw_version_id = selected[dataset]
            record = versions.loc[raw_version_id]
            with gzip.open(record.path, 'rb') as source:
                content = source.read()
            metadata = json.loads(record.metadata_json)
            result = LifecycleSourceResult(
                dataset=dataset,
                scope_start=record.scope_start if pd.notna(record.scope_start) else None,
                scope_end=record.scope_end if pd.notna(record.scope_end) else None,
                content=content,
                metadata=metadata,
            )
            frame = decode_lifecycle_source(result)
            frames.append(normalize_lifecycle_rows(
                frame, dataset, raw_version_id, record.fetched_at,
            ))
        rows = pd.concat(frames, ignore_index=True)
        return build_lifecycle_model(rows), selected

    @staticmethod
    def _blocking_findings(model):
        return [
            finding for finding in model.findings
            if str(finding.severity) in {'blocking', 'quarantine'}
        ]

    def build_and_promote(self):
        model, selected = self._selected_model()
        blocking = self._blocking_findings(model)
        if blocking:
            rules = ', '.join(sorted({finding.rule_id for finding in blocking}))
            raise ValueError(f'차단 또는 격리 항목이 있어 승격할 수 없습니다: {rules}')
        fingerprint = lifecycle_fingerprint(model)
        try:
            verification_model, verification_selection = self._selected_model()
            verification_fingerprint = lifecycle_fingerprint(verification_model)
            deterministic = (
                verification_selection == selected
                and verification_fingerprint == fingerprint
            )
        except Exception as exc:
            verification_fingerprint = None
            deterministic = False
            verification_error = str(exc)
        else:
            verification_error = ''

        if not deterministic:
            message = (
                '승격 전 재빌드 지문이 후보 모델과 불일치합니다.'
                if verification_fingerprint is not None
                else f'승격 전 재빌드 실패: {verification_error}'
            )
            self.store.record_rebuild(
                self.PARSER_VERSION,
                self.RULES_VERSION,
                selected,
                fingerprint,
                verification_fingerprint,
                'failed',
                message,
            )
            raise RuntimeError(message)

        revision = self.store.promote_model(model, selected)
        self.store.record_rebuild(
            self.PARSER_VERSION,
            self.RULES_VERSION,
            selected,
            fingerprint,
            verification_fingerprint,
            'passed',
            '승격 전 선택 원본 재빌드 지문이 일치합니다.',
        )
        return {
            'status': 'promoted',
            'revision': revision,
            'fingerprint': fingerprint,
            'source_selection': selected,
        }

    def rebuild(self):
        expected = self.store.current_fingerprint()
        if expected is None:
            raise ValueError('비교할 승격 모델이 없습니다.')
        try:
            model, selected = self._selected_model()
            actual = lifecycle_fingerprint(model)
            status = 'passed' if actual == expected else 'failed'
            message = (
                '선택 원본 재빌드 지문이 일치합니다.' if status == 'passed'
                else '선택 원본 재빌드 지문이 승격 모델과 불일치합니다.'
            )
        except Exception as exc:
            selected = self.store.source_selection()
            actual = None
            status = 'failed'
            message = f'선택 원본 재빌드 실패: {exc}'
        rebuild_id = self.store.record_rebuild(
            self.PARSER_VERSION,
            self.RULES_VERSION,
            selected,
            expected,
            actual,
            status,
            message,
        )
        return {
            'rebuild_id': rebuild_id,
            'status': status,
            'expected_fingerprint': expected,
            'actual_fingerprint': actual,
            'message': message,
        }
