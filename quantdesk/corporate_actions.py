"""Fail-closed, source-verified split accounting primitives."""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json

import pandas as pd


class CorporateActionError(ValueError):
    pass


@dataclass(frozen=True)
class PriceBasisEvidence:
    code: str
    basis: str
    provider: str
    retrieved_at: str
    source_version: str
    fingerprint: str


@dataclass(frozen=True)
class SplitEvent:
    code: str
    effective_date: str
    published_at: str
    action_type: str
    ratio: Decimal
    status: str
    source_fingerprint: str


@dataclass(frozen=True)
class ResearchActionContext:
    evidence: tuple[PriceBasisEvidence, ...]
    events: tuple[SplitEvent, ...]

    def __post_init__(self):
        object.__setattr__(self, 'evidence', tuple(self.evidence))
        object.__setattr__(self, 'events', tuple(self.events))
        codes = [item.code for item in self.evidence]
        if len(codes) != len(set(codes)):
            raise CorporateActionError('duplicate price basis evidence')
        keys = []
        for event in self.events:
            validate_split_event(event)
            keys.append((event.code, event.effective_date))
        if len(keys) != len(set(keys)):
            raise CorporateActionError('duplicate effective-date split event')

    def evidence_for(self, code: str) -> PriceBasisEvidence:
        matching = [item for item in self.evidence if item.code == code]
        if len(matching) != 1:
            raise CorporateActionError(f'{code}: price basis evidence is missing')
        require_raw_basis(matching[0], code)
        return matching[0]

    def events_for(self, code: str) -> tuple[SplitEvent, ...]:
        return tuple(sorted((event for event in self.events if event.code == code),
                            key=lambda item: (item.effective_date, item.source_fingerprint)))

    def fingerprint(self) -> str:
        payload = {
            'evidence': [asdict(item) for item in sorted(self.evidence, key=lambda item: item.code)],
            'events': [asdict(item) for item in sorted(
                self.events, key=lambda item: (item.code, item.effective_date, item.source_fingerprint)
            )],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorporateActionError(f'{name} is required')
    return value


def _aware_timestamp(value: str, name: str) -> datetime:
    _required(value, name)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CorporateActionError(f'{name} must be an ISO timestamp') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CorporateActionError(f'{name} must have a timezone')
    return parsed


def _iso_date(value: str, name: str) -> date:
    _required(value, name)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CorporateActionError(f'{name} must be an ISO date') from exc


def require_raw_basis(evidence: PriceBasisEvidence, code: str) -> None:
    if not isinstance(evidence, PriceBasisEvidence):
        raise CorporateActionError('price basis evidence is required')
    if _required(evidence.code, 'code') != _required(code, 'code'):
        raise CorporateActionError('price basis code mismatch')
    if evidence.basis != 'raw':
        raise CorporateActionError('verified raw price basis is required')
    for name in ('provider', 'source_version', 'fingerprint'):
        _required(getattr(evidence, name), name)
    _aware_timestamp(evidence.retrieved_at, 'retrieved_at')


def validate_split_event(event: SplitEvent) -> None:
    if not isinstance(event, SplitEvent):
        raise CorporateActionError('split event is required')
    _required(event.code, 'code')
    _iso_date(event.effective_date, 'effective_date')
    _aware_timestamp(event.published_at, 'published_at')
    _required(event.source_fingerprint, 'source_fingerprint')
    if event.action_type not in ('split', 'reverse_split'):
        raise CorporateActionError('unsupported corporate action')
    if event.status != 'validated':
        raise CorporateActionError('validated corporate action is required')
    try:
        ratio = Decimal(str(event.ratio))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CorporateActionError('invalid split ratio') from exc
    if not ratio.is_finite() or ratio <= 0:
        raise CorporateActionError('split ratio must be finite and positive')


def split_adjusted_closes(
    prices: pd.DataFrame,
    evidence: PriceBasisEvidence,
    events: list[SplitEvent],
    signal_at: str,
) -> pd.DataFrame:
    require_raw_basis(evidence, evidence.code)
    signal_time = _aware_timestamp(signal_at, 'signal_at')
    if not isinstance(prices, pd.DataFrame) or not {'Date', 'Close'}.issubset(prices.columns):
        raise CorporateActionError('Date and Close prices are required')
    try:
        dates = prices['Date'].map(lambda value: _iso_date(value, 'Date'))
        closes = prices['Close'].map(lambda value: Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CorporateActionError('invalid raw close price') from exc
    if any(not close.is_finite() or close <= 0 for close in closes):
        raise CorporateActionError('raw close prices must be finite and positive')
    if any(day > signal_time.date() for day in dates):
        raise CorporateActionError('future prices are not available at signal time')

    seen_dates = set()
    eligible = []
    for event in events:
        validate_split_event(event)
        if event.code != evidence.code:
            raise CorporateActionError('split event code mismatch')
        effective = _iso_date(event.effective_date, 'effective_date')
        if effective in seen_dates:
            raise CorporateActionError('duplicate effective-date split event')
        seen_dates.add(effective)
        if effective > signal_time.date():
            continue
        if _aware_timestamp(event.published_at, 'published_at') > signal_time:
            raise CorporateActionError('effective split was not public at signal time')
        eligible.append((effective, Decimal(str(event.ratio))))

    result = prices[['Date', 'Close']].copy(deep=True)
    adjusted = []
    for day, close in zip(dates, closes):
        for effective, ratio in sorted(eligible):
            if day < effective:
                close /= ratio
        adjusted.append(float(close))
    result['Close'] = adjusted
    return result


def convert_split_position(
    quantity: int | Decimal, event: SplitEvent, *, fractional: bool = False,
) -> int | Decimal:
    validate_split_event(event)
    if isinstance(quantity, bool) or not isinstance(quantity, (int, Decimal)):
        raise CorporateActionError('position quantity must be an integer or Decimal')
    amount = Decimal(quantity)
    if not amount.is_finite() or amount <= 0:
        raise CorporateActionError('position quantity must be finite and positive')
    if not fractional and amount != amount.to_integral_value():
        raise CorporateActionError('whole-share position cannot contain fractional shares')
    converted = amount * Decimal(str(event.ratio))
    if fractional:
        return converted
    if converted != converted.to_integral_value():
        raise CorporateActionError('fractional entitlement requires cash-in-lieu evidence')
    return int(converted)


def require_event_date_raw_bar(
    prices: pd.DataFrame, evidence: PriceBasisEvidence, event: SplitEvent,
) -> tuple[float, float]:
    validate_split_event(event)
    require_raw_basis(evidence, event.code)
    if not isinstance(prices, pd.DataFrame) or not {'Date', 'Open', 'Close'}.issubset(prices.columns):
        raise CorporateActionError('raw Date, Open, and Close prices are required')
    dates = prices['Date'].map(lambda value: _iso_date(value, 'Date'))
    matching = prices.loc[dates == _iso_date(event.effective_date, 'effective_date')]
    if len(matching) != 1:
        raise CorporateActionError('exactly one event-date raw bar is required')
    try:
        raw_open = Decimal(str(matching.iloc[0]['Open']))
        raw_close = Decimal(str(matching.iloc[0]['Close']))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CorporateActionError('invalid event-date raw bar') from exc
    if any(not value.is_finite() or value <= 0 for value in (raw_open, raw_close)):
        raise CorporateActionError('event-date raw open and close must be positive')
    return float(raw_open), float(raw_close)
