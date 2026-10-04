"""Pure point-in-time investability status and policy decisions."""

import hashlib
import json
from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class InvestabilityPolicy:
    policy_id: str
    version: str
    allowed_markets: tuple[str, ...]
    allowed_security_kinds: tuple[str, ...]
    blocking_flags: tuple[str, ...]

    @property
    def fingerprint(self):
        payload = json.dumps(asdict(self), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()


INVESTABILITY_V1 = InvestabilityPolicy(
    policy_id='investability-v1',
    version='1.0.0',
    allowed_markets=('KOSPI', 'KOSDAQ'),
    allowed_security_kinds=('common',),
    blocking_flags=(
        'management_designation', 'trading_suspension', 'liquidation_trading',
    ),
)


@dataclass(frozen=True)
class StatusFinding:
    severity: str
    rule_id: str
    message: str
    security_id: str | None = None
    requested_date: str | None = None
    evidence_id: str | None = None


@dataclass(frozen=True)
class StatusModel:
    assertions: pd.DataFrame
    intervals: pd.DataFrame
    listings: pd.DataFrame
    sessions: tuple[str, ...]
    coverage_dates: tuple[str, ...]
    findings: tuple[StatusFinding, ...]


@dataclass(frozen=True)
class InvestabilityDecision:
    security_id: str
    as_of_timestamp: str
    state: str
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    policy_fingerprint: str
    model_fingerprint: str


_ASSERTION_COLUMNS = [
    'evidence_id', 'security_id', 'requested_date', 'effective_from',
    'known_at', 'observed_at', 'market', 'security_kind',
    'management_designation', 'trading_suspension', 'liquidation_trading',
    'raw_version_id', 'source_row_number',
]


def _dates(frame):
    column = 'date' if 'date' in frame else 'Date'
    values = pd.to_datetime(frame[column], errors='coerce')
    if values.isna().any():
        raise ValueError('공식 거래일 형식이 올바르지 않습니다.')
    return tuple(sorted(values.dt.date.astype(str).unique()))


def _timestamp(value):
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize('Asia/Seoul')
    return stamp


def _next_session(day, session_dates):
    return next((item for item in session_dates if item > day), None)


def _known_at(row, session_dates):
    value = row.get('published_at')
    if pd.notna(value) and str(value).strip():
        return _timestamp(value).isoformat()
    next_day = _next_session(row['effective_date'], session_dates)
    if next_day is None:
        return None
    return pd.Timestamp(f'{next_day}T00:00:00', tz='Asia/Seoul').isoformat()


def _evidence_id(row):
    value = (
        f"investability-v1:{row['dataset']}:{row['requested_date']}:"
        f"{int(row['raw_version_id'])}:{int(row['source_row_number'])}"
    )
    return 'inv_' + hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]


def _listed_for(reader, day):
    if hasattr(reader, 'listed_securities'):
        return reader.listed_securities(day)
    if hasattr(reader, 'listed_on'):
        return reader.listed_on(day)
    raise TypeError('생애주기 조회기는 listed_securities 또는 listed_on을 제공해야 합니다.')


def _match_security(row, listed):
    standard = row.get('standard_code')
    if pd.notna(standard) and str(standard).strip() and 'standard_code' in listed:
        matches = listed[listed.standard_code.eq(str(standard).strip())]
    else:
        matches = listed[listed.short_code.astype(str).str.zfill(6).eq(row['short_code'])]
    return matches


def build_status_model(rows: pd.DataFrame, lifecycle_reader,
                       sessions: pd.DataFrame) -> StatusModel:
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        raise ValueError('투자 가능성 상태 행이 없습니다.')
    session_dates = _dates(sessions)
    required = {
        'dataset', 'requested_date', 'raw_version_id', 'source_row_number',
        'observed_at', 'standard_code', 'short_code', 'market', 'security_kind',
        'management_designation', 'trading_suspension', 'liquidation_trading',
        'effective_date', 'published_at',
    }
    missing = required - set(rows.columns)
    if missing:
        raise ValueError(f'정규화된 상태 열이 없습니다: {", ".join(sorted(missing))}')

    source = rows.copy()
    source['requested_date'] = pd.to_datetime(source.requested_date).dt.date.astype(str)
    source['effective_date'] = pd.to_datetime(source.effective_date).dt.date.astype(str)
    source['short_code'] = source.short_code.astype(str).str.zfill(6)
    source = source.sort_values(
        ['requested_date', 'short_code', 'raw_version_id', 'source_row_number'],
        kind='stable',
    ).reset_index(drop=True)
    coverage_dates = tuple(sorted(source.requested_date.unique()))
    findings = []
    assertion_records = []
    listing_records = []

    for day in coverage_dates:
        listed = _listed_for(lifecycle_reader, day).copy()
        if not listed.empty:
            listed['requested_date'] = day
            listing_records.extend(listed.to_dict(orient='records'))
        for _, row in source[source.requested_date.eq(day)].iterrows():
            evidence_id = _evidence_id(row)
            matches = _match_security(row, listed)
            if len(matches) != 1:
                findings.append(StatusFinding(
                    'blocking', 'ambiguous_lifecycle_identity',
                    '공식 상태 행을 하나의 생애주기 종목과 연결할 수 없습니다.',
                    requested_date=day, evidence_id=evidence_id,
                ))
                continue
            identity = matches.iloc[0]
            assertion_records.append({
                'evidence_id': evidence_id,
                'security_id': identity.security_id,
                'requested_date': day,
                'effective_from': row.effective_date,
                'known_at': _known_at(row, session_dates),
                'observed_at': _timestamp(row.observed_at).isoformat(),
                'market': str(identity.market),
                'security_kind': str(identity.security_kind),
                'management_designation': bool(row.management_designation),
                'trading_suspension': bool(row.trading_suspension),
                'liquidation_trading': bool(row.liquidation_trading),
                'raw_version_id': int(row.raw_version_id),
                'source_row_number': int(row.source_row_number),
            })

    assertions = pd.DataFrame(assertion_records, columns=_ASSERTION_COLUMNS)
    flag_columns = list(INVESTABILITY_V1.blocking_flags)
    if not assertions.empty:
        for keys, group in assertions.groupby(
            ['security_id', 'requested_date', 'effective_from'], sort=True,
        ):
            if len(group[flag_columns].drop_duplicates()) > 1:
                findings.append(StatusFinding(
                    'blocking', 'contradictory_assertions',
                    '같은 효력일의 공식 상태가 서로 충돌합니다.',
                    security_id=keys[0], requested_date=keys[1],
                    evidence_id=group.evidence_id.iloc[0],
                ))

    intervals = assertions.copy()
    if not intervals.empty:
        intervals['effective_to'] = intervals.requested_date.map(
            lambda day: _next_session(day, session_dates)
        )
        intervals = intervals[
            ['evidence_id', 'security_id', 'effective_from', 'effective_to',
             'known_at', 'market', 'security_kind', *flag_columns,
             'raw_version_id', 'source_row_number']
        ]
    listings = pd.DataFrame(listing_records)
    return StatusModel(
        assertions=assertions.reset_index(drop=True),
        intervals=intervals.reset_index(drop=True),
        listings=listings.reset_index(drop=True),
        sessions=session_dates,
        coverage_dates=coverage_dates,
        findings=tuple(findings),
    )


def _canonical_frame(frame):
    if frame is None or frame.empty:
        return []
    data = frame.copy().where(pd.notna(frame), None)
    columns = sorted(data.columns)
    records = data[columns].to_dict(orient='records')
    return sorted(records, key=lambda item: json.dumps(
        item, sort_keys=True, ensure_ascii=False, default=str,
    ))


def investability_fingerprint(model: StatusModel,
                              policy: InvestabilityPolicy) -> str:
    payload = {
        'assertions': _canonical_frame(model.assertions),
        'intervals': _canonical_frame(model.intervals),
        'listings': _canonical_frame(model.listings),
        'sessions': list(model.sessions),
        'coverage_dates': list(model.coverage_dates),
        'findings': [asdict(item) for item in sorted(
            model.findings,
            key=lambda value: (
                value.rule_id, value.security_id or '', value.requested_date or '',
                value.evidence_id or '',
            ),
        )],
        'policy': asdict(policy),
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str,
    )
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _decision(model, policy, security_id, stamp, state, reasons, evidence=()):
    return InvestabilityDecision(
        security_id=security_id,
        as_of_timestamp=stamp.isoformat(),
        state=state,
        reason_codes=tuple(reasons),
        evidence_ids=tuple(evidence),
        policy_fingerprint=policy.fingerprint,
        model_fingerprint=investability_fingerprint(model, policy),
    )


def _has_continuous_coverage(model, start_day, end_day):
    required = [day for day in model.sessions if start_day <= day <= end_day]
    return set(required).issubset(model.coverage_dates)


def decision_at(model: StatusModel, security_id: str, as_of_timestamp: str,
                policy: InvestabilityPolicy) -> InvestabilityDecision:
    stamp = _timestamp(as_of_timestamp)
    day = stamp.date().isoformat()
    if day not in model.coverage_dates:
        return _decision(
            model, policy, security_id, stamp, 'unknown', ('missing_daily_coverage',),
        )
    listed = model.listings
    listed_today = listed[
        listed.requested_date.eq(day) & listed.security_id.eq(security_id)
    ] if not listed.empty else listed
    if listed_today.empty:
        return _decision(model, policy, security_id, stamp, 'not_listed', ('not_listed',))

    assertions = model.assertions
    candidates = assertions[
        assertions.security_id.eq(security_id)
        & assertions.effective_from.le(day)
        & assertions.requested_date.le(day)
        & assertions.known_at.notna()
    ].copy()
    if not candidates.empty:
        candidates = candidates[candidates.known_at.map(_timestamp).le(stamp)]
    candidates = candidates[
        candidates.requested_date.map(
            lambda start: _has_continuous_coverage(model, start, day)
        )
    ] if not candidates.empty else candidates
    if candidates.empty:
        return _decision(
            model, policy, security_id, stamp, 'unknown', ('evidence_not_yet_known',),
        )
    latest_date = candidates.requested_date.max()
    latest = candidates[candidates.requested_date.eq(latest_date)]
    latest_known = latest.known_at.map(_timestamp).max()
    latest = latest[latest.known_at.map(_timestamp).eq(latest_known)]
    flag_columns = list(policy.blocking_flags)
    if len(latest[flag_columns].drop_duplicates()) > 1:
        return _decision(
            model, policy, security_id, stamp, 'unknown',
            ('contradictory_assertions',), latest.evidence_id,
        )
    row = latest.sort_values(['raw_version_id', 'source_row_number']).iloc[-1]
    reasons = []
    if row.market not in policy.allowed_markets:
        reasons.append('market_not_allowed')
    if row.security_kind not in policy.allowed_security_kinds:
        reasons.append('security_kind_not_allowed')
    reasons.extend(flag for flag in policy.blocking_flags if bool(row[flag]))
    state = 'ineligible' if reasons else 'eligible'
    return _decision(
        model, policy, security_id, stamp, state, reasons,
        latest.evidence_id.tolist(),
    )


def reconcile_investability(model: StatusModel, lifecycle_reader,
                            daily_market: pd.DataFrame, start_date: str,
                            end_date: str,
                            policy: InvestabilityPolicy):
    start = pd.Timestamp(start_date).date().isoformat()
    end = pd.Timestamp(end_date).date().isoformat()
    selected_days = [day for day in model.sessions if start <= day <= end]
    market = daily_market.copy() if isinstance(daily_market, pd.DataFrame) else pd.DataFrame()
    required_market = {'trade_date', 'code'}
    if not market.empty and not required_market.issubset(market.columns):
        missing = required_market - set(market.columns)
        raise ValueError(f'일별 시장 필수 열이 없습니다: {", ".join(sorted(missing))}')
    if not market.empty:
        market['trade_date'] = pd.to_datetime(market.trade_date).dt.date.astype(str)
        market['code'] = market.code.astype(str).str.zfill(6)
        market = market[market.trade_date.between(start, end)].copy()

    records = []
    findings = []
    listed_by_day = {}
    for day in selected_days:
        listed = _listed_for(lifecycle_reader, day).copy()
        if not listed.empty:
            listed['short_code'] = listed.short_code.astype(str).str.zfill(6)
        listed_by_day[day] = listed
        assertions = model.assertions[model.assertions.requested_date.eq(day)]
        active_ids = set(listed.security_id) if not listed.empty else set()
        for row in assertions.itertuples(index=False):
            if row.security_id not in active_ids:
                findings.append(StatusFinding(
                    'blocking', 'status_outside_listing',
                    '공식 상태가 유효한 상장 생애주기 밖에 있습니다.',
                    row.security_id, day, row.evidence_id,
                ))

        eligible_listings = listed[
            listed.market.isin(policy.allowed_markets)
            & listed.security_kind.isin(policy.allowed_security_kinds)
        ] if not listed.empty else listed
        asserted_ids = set(assertions.security_id)
        for identity in eligible_listings.itertuples(index=False):
            if day not in model.coverage_dates or identity.security_id not in asserted_ids:
                reason = 'missing_selected_daily_status'
                records.append({
                    'requested_date': day,
                    'security_id': identity.security_id,
                    'state': 'unknown',
                    'reason_codes': (reason,),
                    'evidence_ids': (),
                })
                findings.append(StatusFinding(
                    'blocking', reason,
                    '상장 종목의 선택된 공식 일별 상태가 없습니다.',
                    identity.security_id, day,
                ))
                continue
            decision = decision_at(
                model, identity.security_id,
                f'{day}T23:59:59+09:00', policy,
            )
            records.append({
                'requested_date': day,
                'security_id': identity.security_id,
                'state': decision.state,
                'reason_codes': decision.reason_codes,
                'evidence_ids': decision.evidence_ids,
            })

    if not market.empty:
        for row in market.itertuples(index=False):
            listed = listed_by_day.get(row.trade_date)
            matches = listed[
                listed.short_code.eq(row.code)
            ] if listed is not None and not listed.empty else pd.DataFrame()
            if len(matches) != 1:
                raw_version = getattr(row, 'raw_version_id', '')
                findings.append(StatusFinding(
                    'quarantine', 'market_without_lifecycle',
                    '일별 시장 행을 하나의 상장 생애주기와 연결할 수 없습니다.',
                    requested_date=row.trade_date,
                    evidence_id=f'market:{raw_version}:{row.trade_date}:{row.code}',
                ))

        if 'close' in market:
            ordered = market.sort_values(['code', 'trade_date'], kind='stable')
            for code, group in ordered.groupby('code', sort=True):
                previous = None
                for row in group.itertuples(index=False):
                    close = float(row.close)
                    if previous not in (None, 0) and (close / previous < 0.5 or close / previous > 2.0):
                        listed = listed_by_day.get(row.trade_date)
                        matched = listed[
                            listed.short_code.eq(code)
                        ] if listed is not None and not listed.empty else pd.DataFrame()
                        security_id = matched.iloc[0].security_id if len(matched) == 1 else None
                        findings.append(StatusFinding(
                            'warning', 'price_gap',
                            '큰 가격 변동은 공식 상태를 대체하지 않으며 별도 확인이 필요합니다.',
                            security_id, row.trade_date,
                        ))
                    previous = close

    columns = [
        'requested_date', 'security_id', 'state', 'reason_codes', 'evidence_ids',
    ]
    return pd.DataFrame(records, columns=columns), tuple(findings)
