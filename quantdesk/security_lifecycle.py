"""Pure point-in-time security identity and lifecycle construction."""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

import pandas as pd


class FindingLevel(StrEnum):
    PASS = 'pass'
    WARNING = 'warning'
    QUARANTINE = 'quarantine'
    BLOCKING = 'blocking'


@dataclass(frozen=True)
class LifecycleFinding:
    severity: str
    rule_id: str
    message: str
    security_id: str | None = None
    effective_date: str | None = None
    lifecycle_raw_version_id: int | None = None
    market_raw_version_id: int | None = None
    lifecycle_value: str | None = None
    market_value: str | None = None


@dataclass(frozen=True)
class LifecycleModel:
    issuers: pd.DataFrame
    securities: pd.DataFrame
    identifiers: pd.DataFrame
    names: pd.DataFrame
    events: pd.DataFrame
    lineage: pd.DataFrame
    intervals: pd.DataFrame
    findings: tuple[LifecycleFinding, ...]

    def listed_on(self, as_of_date):
        day = pd.Timestamp(as_of_date).date().isoformat()
        frame = self.intervals
        visible = frame[
            frame.valid_from.le(day)
            & (frame.valid_to.isna() | frame.valid_to.gt(day))
        ].copy()
        return visible.sort_values(['market', 'short_code']).reset_index(drop=True)


_COLUMN_ALIASES = {
    '표준코드': 'standard_code', 'ISU_CD': 'standard_code', 'StandardCode': 'standard_code',
    '단축코드': 'short_code', '종목코드': 'short_code', 'ISU_SRT_CD': 'short_code',
    '한글 종목명': 'name', '한글종목명': 'name', '종목명': 'name', 'ISU_NM': 'name',
    '시장구분': 'market', 'MKT_NM': 'market',
    '증권구분': 'security_type', 'SECUGRP_NM': 'security_type',
    '주식종류': 'stock_type', 'KIND_STKCERT_TP_NM': 'stock_type',
    '상장일': 'listing_date', 'LIST_DD': 'listing_date',
    '최종매매일': 'last_trading_date', 'ARRANTRD_END_DD': 'last_trading_date',
    '폐지일': 'delisting_date', '상장폐지일': 'delisting_date', 'DELIST_DD': 'delisting_date',
    '회사코드': 'issuer_reference', '법인코드': 'issuer_reference',
    '변경구분': 'event_type', '변경일': 'effective_date',
    '이전종목코드': 'previous_code', '이전종목명': 'previous_name',
    '이전시장': 'previous_market', '승계표준코드': 'successor_standard_code',
}

_CANONICAL_COLUMNS = [
    'dataset', 'raw_version_id', 'source_row_number', 'observed_at',
    'standard_code', 'short_code', 'name', 'market', 'security_type', 'stock_type',
    'listing_date', 'last_trading_date', 'delisting_date', 'issuer_reference',
    'event_type', 'effective_date', 'previous_code', 'previous_name',
    'previous_market', 'successor_standard_code',
]


def _date_series(values):
    parsed = pd.to_datetime(values.replace('', None), errors='coerce')
    return parsed.dt.strftime('%Y-%m-%d').where(parsed.notna(), None)


def _short_code(value):
    text = str(value or '').strip()
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(6) if text else ''


def normalize_lifecycle_rows(frame, dataset, raw_version_id, observed_at):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError('생애주기 원본이 비어 있습니다.')
    data = frame.rename(columns=_COLUMN_ALIASES).copy()
    required = {'standard_code', 'short_code', 'name', 'market', 'stock_type', 'listing_date'}
    missing = required - set(data.columns)
    if missing:
        labels = {'standard_code': '표준코드'}
        missing_text = ', '.join(labels.get(item, item) for item in sorted(missing))
        raise ValueError(f'생애주기 필수 열이 없습니다: {missing_text}')
    for column in _CANONICAL_COLUMNS:
        if column not in data:
            data[column] = None
    data['dataset'] = dataset
    data['raw_version_id'] = int(raw_version_id)
    data['source_row_number'] = range(1, len(data) + 1)
    data['observed_at'] = pd.Timestamp(observed_at).isoformat()
    for column in [
        'standard_code', 'name', 'market', 'security_type', 'stock_type',
        'issuer_reference', 'event_type', 'previous_name', 'previous_market',
        'successor_standard_code',
    ]:
        data[column] = data[column].fillna('').astype(str).str.strip().replace('', None)
    data['short_code'] = data.short_code.map(_short_code)
    data['previous_code'] = data.previous_code.map(_short_code).replace('', None)
    data['market'] = data.market.str.upper()
    data.loc[data.market.str.startswith('KOSPI', na=False), 'market'] = 'KOSPI'
    data.loc[data.market.str.startswith('KOSDAQ', na=False), 'market'] = 'KOSDAQ'
    for column in ['listing_date', 'last_trading_date', 'delisting_date', 'effective_date']:
        data[column] = _date_series(data[column])
    defaults = {
        'security_master': 'listing', 'new_listings': 'listing',
        'delistings': 'delisting', 'identifier_changes': 'identifier_change',
    }
    data['event_type'] = data.event_type.fillna(defaults.get(dataset))
    data['effective_date'] = data.effective_date.fillna(
        data.delisting_date.where(data.event_type.eq('delisting'), data.listing_date)
    )
    if data.standard_code.isna().any() or data.standard_code.eq('').any():
        raise ValueError('표준코드 값이 비어 있습니다.')
    if data.listing_date.isna().any() or data.effective_date.isna().any():
        raise ValueError('상장일 또는 사건 효력일이 유효하지 않습니다.')
    return data[_CANONICAL_COLUMNS].reset_index(drop=True)


def _stable_id(prefix, value):
    digest = hashlib.sha256(f'lifecycle-v1:{value}'.encode('utf-8')).hexdigest()[:20]
    return f'{prefix}_{digest}'


def _classification(stock_type):
    text = str(stock_type or '').strip().lower()
    if text in {'보통주', 'common', 'common stock'}:
        return 'common'
    if text in {'우선주', 'preferred', 'preferred stock'}:
        return 'preferred'
    if '스팩' in text or text == 'spac':
        return 'spac'
    if '리츠' in text or text == 'reit':
        return 'reit'
    return 'unknown'


def _empty(columns):
    return pd.DataFrame(columns=columns)


def build_lifecycle_model(source_rows):
    if not isinstance(source_rows, pd.DataFrame) or source_rows.empty:
        raise ValueError('생애주기 원본 행이 없습니다.')
    missing = set(_CANONICAL_COLUMNS) - set(source_rows.columns)
    if missing:
        raise ValueError(f'정규화된 생애주기 열이 없습니다: {", ".join(sorted(missing))}')
    rows = source_rows[_CANONICAL_COLUMNS].copy()
    rows = rows.sort_values(
        ['standard_code', 'effective_date', 'dataset', 'raw_version_id', 'source_row_number'],
        kind='stable',
    ).reset_index(drop=True)
    findings = []
    issuer_records, security_records, event_records, interval_records = [], [], [], []

    for standard_code, group in rows.groupby('standard_code', sort=True):
        group = group.sort_values(['effective_date', 'raw_version_id', 'source_row_number'])
        first = group.iloc[0]
        security_id = _stable_id('sec', standard_code)
        issuer_ref = next((x for x in group.issuer_reference if pd.notna(x) and x), None)
        issuer_id = _stable_id('iss', issuer_ref) if issuer_ref else None
        if issuer_ref:
            issuer_row = group[group.issuer_reference.eq(issuer_ref)].iloc[0]
            issuer_records.append({
                'issuer_id': issuer_id,
                'official_reference': issuer_ref,
                'raw_version_id': int(issuer_row.raw_version_id),
                'source_row_number': int(issuer_row.source_row_number),
            })
        stock_type = next((x for x in reversed(group.stock_type.tolist()) if pd.notna(x)), None)
        kind = _classification(stock_type)
        security_records.append({
            'security_id': security_id, 'issuer_id': issuer_id,
            'standard_code': standard_code, 'security_kind': kind,
            'raw_version_id': int(first.raw_version_id),
            'source_row_number': int(first.source_row_number),
        })
        if kind == 'unknown':
            findings.append(LifecycleFinding(
                'blocking', 'unknown_classification',
                '공식 증권 분류를 확정할 수 없습니다.', security_id,
            ))
        unsupported = group[~group.event_type.isin(
            ['listing', 'identifier_change', 'name_change', 'market_transfer', 'delisting']
        )]
        if not unsupported.empty:
            findings.append(LifecycleFinding(
                'blocking', 'unsupported_lineage',
                '후속 기업행사 단계가 필요한 연결 사건입니다.', security_id,
                unsupported.iloc[0].effective_date,
            ))

        listing_date = min(group.listing_date.dropna())
        delisting_values = group.delisting_date.dropna().tolist()
        delisting_date = min(delisting_values) if delisting_values else None
        current = {
            'short_code': first.previous_code or first.short_code,
            'name': first.previous_name or first['name'],
            'market': first.previous_market or first.market,
            'security_kind': kind,
            'raw_version_id': int(first.raw_version_id),
            'source_row_number': int(first.source_row_number),
        }
        changes = []
        for row in group.itertuples(index=False):
            evidence = {
                'raw_version_id': int(row.raw_version_id),
                'source_row_number': int(row.source_row_number),
            }
            if row.event_type != 'delisting' and row.effective_date > listing_date:
                changes.append((row.effective_date, {
                    'short_code': row.short_code or current['short_code'],
                    'name': row.name or current['name'],
                    'market': row.market or current['market'],
                    'security_kind': _classification(row.stock_type),
                    **evidence,
                }))
            if row.event_type not in {'listing'}:
                event_records.append({
                    'security_id': security_id, 'event_type': row.event_type,
                    'effective_date': row.effective_date,
                    'last_trading_date': row.last_trading_date,
                    'successor_standard_code': row.successor_standard_code,
                    **evidence,
                })
        event_records.append({
            'security_id': security_id, 'event_type': 'listing',
            'effective_date': listing_date, 'last_trading_date': None,
            'successor_standard_code': None,
            'raw_version_id': int(first.raw_version_id),
            'source_row_number': int(first.source_row_number),
        })
        points = [(listing_date, current)]
        points.extend(changes)
        points = sorted({date: state for date, state in points}.items())
        for index, (valid_from, state) in enumerate(points):
            valid_to = points[index + 1][0] if index + 1 < len(points) else delisting_date
            if valid_to is not None and valid_to <= valid_from:
                findings.append(LifecycleFinding(
                    'blocking', 'invalid_interval', '생애주기 유효기간 순서가 잘못되었습니다.',
                    security_id, valid_from,
                ))
                continue
            interval_records.append({
                'security_id': security_id, 'issuer_id': issuer_id,
                'standard_code': standard_code, **state,
                'valid_from': valid_from, 'valid_to': valid_to,
            })

    intervals = pd.DataFrame(interval_records)
    if not intervals.empty:
        for code, group in intervals.groupby('short_code'):
            ordered = group.sort_values('valid_from')
            previous = None
            for row in ordered.itertuples(index=False):
                if previous is not None:
                    previous_end = previous.valid_to or '9999-12-31'
                    if row.security_id != previous.security_id and row.valid_from < previous_end:
                        findings.append(LifecycleFinding(
                            'blocking', 'overlapping_code',
                            f'같은 종목코드 {code}의 유효기간이 겹칩니다.', row.security_id,
                            row.valid_from,
                        ))
                if previous is None or (row.valid_to or '9999-12-31') > (previous.valid_to or '9999-12-31'):
                    previous = row

    securities = pd.DataFrame(security_records).drop_duplicates('security_id').sort_values('security_id')
    issuers = pd.DataFrame(issuer_records).drop_duplicates('issuer_id').sort_values('issuer_id') \
        if issuer_records else _empty([
            'issuer_id', 'official_reference', 'raw_version_id', 'source_row_number',
        ])
    intervals = intervals.sort_values(['security_id', 'valid_from']).reset_index(drop=True)
    identifiers = intervals[[
        'security_id', 'standard_code', 'short_code', 'valid_from', 'valid_to',
        'raw_version_id', 'source_row_number',
    ]].copy()
    names = intervals[[
        'security_id', 'name', 'valid_from', 'valid_to',
        'raw_version_id', 'source_row_number',
    ]].copy()
    events = pd.DataFrame(event_records).drop_duplicates(
        ['security_id', 'event_type', 'effective_date'], keep='last',
    ).sort_values(['effective_date', 'security_id', 'event_type']).reset_index(drop=True)
    lineage = _empty([
        'predecessor_security_id', 'successor_security_id', 'relationship_type',
        'effective_date', 'raw_version_id', 'source_row_number',
    ])
    return LifecycleModel(
        issuers.reset_index(drop=True), securities.reset_index(drop=True),
        identifiers.reset_index(drop=True), names.reset_index(drop=True), events,
        lineage, intervals, tuple(findings),
    )


def lifecycle_fingerprint(model):
    payload = {}
    for name in ['issuers', 'securities', 'identifiers', 'names', 'events', 'lineage', 'intervals']:
        frame = getattr(model, name).copy()
        if frame.empty:
            payload[name] = []
            continue
        frame = frame.reindex(sorted(frame.columns), axis=1)
        frame = frame.fillna('').astype(str).sort_values(list(frame.columns), kind='stable')
        payload[name] = frame.to_dict(orient='records')
    payload['findings'] = sorted(
        [finding.__dict__ for finding in model.findings],
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
    )
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def reconcile_daily_market(store, start_date, end_date):
    """Compare official lifecycle membership with canonical daily-market evidence."""
    start = pd.Timestamp(start_date).date().isoformat()
    end = pd.Timestamp(end_date).date().isoformat()
    with store.connect() as db:
        dates = [row[0] for row in db.execute(
            '''SELECT DISTINCT trade_date FROM historical_raw_versions
            WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date''', (start, end),
        ).fetchall()]
        market = pd.read_sql_query(
            '''SELECT trade_date,code,name,market,close,volume,listed_shares,raw_version_id
            FROM historical_daily_market WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date,code''', db, params=(start, end),
        )

    findings = []
    unresolved_dates = set()
    covered_dates = 0
    for trade_date in dates:
        day = market[market.trade_date.eq(trade_date)].copy()
        if not day.empty:
            covered_dates += 1
        active = store.listed_securities(trade_date)
        active_by_code = {
            str(row.short_code).zfill(6): row for row in active.itertuples(index=False)
        }
        market_by_code = {
            str(row.code).zfill(6): row for row in day.itertuples(index=False)
        }
        for code, row in market_by_code.items():
            lifecycle = active_by_code.get(code)
            if lifecycle is None:
                findings.append(LifecycleFinding(
                    'blocking', 'market_without_lifecycle',
                    f'{trade_date} 시장 원본의 {code} 종목에 활성 생애주기가 없습니다.',
                    effective_date=trade_date,
                    market_raw_version_id=int(row.raw_version_id),
                    market_value=f'{row.name}/{row.market}',
                ))
                unresolved_dates.add(trade_date)
                continue
            if str(lifecycle.name) != str(row.name):
                findings.append(LifecycleFinding(
                    'warning', 'name_conflict',
                    f'{trade_date} {code}의 공식 명칭과 시장 명칭이 다릅니다.',
                    lifecycle.security_id, trade_date,
                    int(lifecycle.raw_version_id), int(row.raw_version_id),
                    str(lifecycle.name), str(row.name),
                ))
                unresolved_dates.add(trade_date)
            if str(lifecycle.market) != str(row.market):
                findings.append(LifecycleFinding(
                    'warning', 'market_conflict',
                    f'{trade_date} {code}의 공식 시장과 일별 시장 값이 다릅니다.',
                    lifecycle.security_id, trade_date,
                    int(lifecycle.raw_version_id), int(row.raw_version_id),
                    str(lifecycle.market), str(row.market),
                ))
                unresolved_dates.add(trade_date)
        for code, lifecycle in active_by_code.items():
            if code not in market_by_code:
                findings.append(LifecycleFinding(
                    'warning', 'active_security_missing',
                    f'{trade_date} 활성 종목 {code}가 일별 시장 원본에 없습니다.',
                    lifecycle.security_id, trade_date,
                    int(lifecycle.raw_version_id), None,
                    f'{lifecycle.name}/{lifecycle.market}', None,
                ))
                unresolved_dates.add(trade_date)

    corporate_action_pending = False
    if not market.empty:
        for code, group in market.groupby('code', sort=True):
            ordered = group.sort_values('trade_date')
            previous = None
            for row in ordered.itertuples(index=False):
                if previous is not None:
                    if previous.listed_shares > 0:
                        share_change = abs(row.listed_shares - previous.listed_shares) / previous.listed_shares
                        if share_change >= 0.20:
                            corporate_action_pending = True
                            findings.append(LifecycleFinding(
                                'blocking', 'listed_shares_jump',
                                f'{row.trade_date} {code} 상장주식수가 20% 이상 변했습니다.',
                                effective_date=row.trade_date,
                                market_raw_version_id=int(row.raw_version_id),
                                lifecycle_value=str(previous.listed_shares),
                                market_value=str(row.listed_shares),
                            ))
                    if previous.close > 0:
                        close_ratio = row.close / previous.close
                        if close_ratio < 0.5 or close_ratio > 2.0:
                            corporate_action_pending = True
                            findings.append(LifecycleFinding(
                                'blocking', 'close_ratio_jump',
                                f'{row.trade_date} {code} 종가 비율이 검토 범위를 벗어났습니다.',
                                effective_date=row.trade_date,
                                market_raw_version_id=int(row.raw_version_id),
                                lifecycle_value=str(previous.close),
                                market_value=str(row.close),
                            ))
                previous = row

    coverage = covered_dates / len(dates) if dates else 0.0
    store.save_reconciliation(
        start, end, findings, coverage, sorted(unresolved_dates), corporate_action_pending,
    )
    return tuple(findings)
