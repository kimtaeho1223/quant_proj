"""Point-in-time input contract for deterministic backtests."""
import hashlib
import json

import pandas as pd


class BacktestDataError(ValueError):
    pass


SNAPSHOT_COLUMNS = {
    'Code', 'Name', 'Market', 'SecurityType', 'Marcap', 'Amount',
}
PRICE_COLUMNS = {'Date', 'Open', 'Close'}


def _iso_date(value, label):
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        raise BacktestDataError(f'{label} 날짜가 올바르지 않습니다.') from None
    if pd.isna(timestamp):
        raise BacktestDataError(f'{label} 날짜가 올바르지 않습니다.')
    return timestamp.date().isoformat()


def _normalize_calendar(calendar):
    values = calendar['date'].tolist() if isinstance(calendar, pd.DataFrame) else list(calendar)
    dates = [_iso_date(value, '거래일') for value in values]
    if len(dates) != len(set(dates)):
        raise BacktestDataError('거래일 중복이 있습니다.')
    return pd.DataFrame({'date': sorted(dates)})


def _normalize_snapshot(frame):
    if not isinstance(frame, pd.DataFrame) or not SNAPSHOT_COLUMNS.issubset(frame.columns):
        missing = sorted(SNAPSHOT_COLUMNS - set(getattr(frame, 'columns', [])))
        raise BacktestDataError(f"종목군 필수 열이 없습니다: {', '.join(missing)}")
    data = frame.copy()
    data['Code'] = data.Code.astype(str).str.zfill(6)
    if data.Code.duplicated().any():
        raise BacktestDataError('종목군 코드가 중복되었습니다.')
    return data.reset_index(drop=True)


def _normalize_prices(frame, label):
    if not isinstance(frame, pd.DataFrame) or not PRICE_COLUMNS.issubset(frame.columns):
        raise BacktestDataError(f'{label}: 가격 필수 열이 없습니다.')
    data = frame.copy()
    data['Date'] = data.Date.map(lambda value: _iso_date(value, f'{label} 가격'))
    if data.Date.duplicated().any():
        raise BacktestDataError(f'{label}: 가격 날짜가 중복되었습니다.')
    for column in ('Open', 'Close'):
        data[column] = pd.to_numeric(data[column], errors='coerce')
    if data[['Open', 'Close']].isna().any().any() or (data[['Open', 'Close']] <= 0).any().any():
        raise BacktestDataError(f'{label}: 가격이 유효하지 않습니다.')
    return data.sort_values('Date').reset_index(drop=True)


def _canonical_frame(frame):
    if frame is None or frame.empty:
        return []
    data = frame.copy()
    data = data.reindex(sorted(data.columns), axis=1)
    data = data.where(pd.notna(data), None)
    records = data.to_dict(orient='records')
    return sorted(records, key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False, default=str))


class BacktestDataset:
    def __init__(self, calendar, snapshots, prices, statements, companies,
                 indices, corporate_actions):
        self.calendar = _normalize_calendar(calendar)
        self.snapshots = {
            _iso_date(date, '종목군'): _normalize_snapshot(frame)
            for date, frame in snapshots.items()
        }
        self.prices = {
            str(code).zfill(6): _normalize_prices(frame, str(code).zfill(6))
            for code, frame in prices.items()
        }
        self.statements = [dict(statement) for statement in statements]
        self.companies = {str(code).zfill(6): dict(value) for code, value in companies.items()}
        self.indices = {
            str(symbol): _normalize_prices(frame, str(symbol))
            for symbol, frame in indices.items()
        }
        self.corporate_actions = corporate_actions.copy() if isinstance(corporate_actions, pd.DataFrame) else pd.DataFrame()

    def schedule(self, start, end):
        first = _iso_date(start, '시작일')
        last = _iso_date(end, '종료일')
        if first > last:
            raise BacktestDataError('시작일은 종료일보다 늦을 수 없습니다.')
        sessions = self.calendar.loc[
            self.calendar.date.between(first, last), 'date'
        ].sort_values()
        if sessions.empty:
            return pd.DataFrame(columns=['signal_date', 'execution_date'])
        periods = pd.to_datetime(sessions).dt.to_period('W-FRI')
        signals = sessions.groupby(periods).last().tolist()
        all_sessions = self.calendar.date.tolist()
        rows = []
        for signal in signals:
            execution = next((day for day in all_sessions if day > signal), None)
            if execution:
                rows.append({'signal_date': signal, 'execution_date': execution})
        return pd.DataFrame(rows, columns=['signal_date', 'execution_date'])

    def week_inputs(self, signal_date):
        signal = _iso_date(signal_date, '신호일')
        if signal not in set(self.calendar.date):
            raise BacktestDataError('신호일이 거래일 달력에 없습니다.')
        if signal not in self.snapshots:
            if any(date > signal for date in self.snapshots):
                raise BacktestDataError('미래 종목군 스냅샷은 사용할 수 없습니다.')
            raise BacktestDataError('신호일의 과거 종목군 스냅샷이 없습니다.')
        execution = next((day for day in self.calendar.date if day > signal), None)
        if execution is None:
            raise BacktestDataError('다음 거래일이 없습니다.')

        listing = self.snapshots[signal].copy()
        codes = listing.Code.tolist()
        histories = {}
        execution_open = {}
        valuation_closes = {}
        issues = []
        for code in codes:
            prices = self.prices.get(code)
            if prices is None:
                issues.append(f'{code}: 주가 없음')
                continue
            histories[code] = prices[prices.Date <= signal].copy()
            open_row = prices[prices.Date == execution]
            if open_row.empty:
                issues.append(f'{code}: 다음 거래일 시가 없음')
            else:
                execution_open[code] = float(open_row.Open.iloc[-1])
            valuation_closes[code] = prices.loc[prices.Date >= execution, ['Date', 'Close']].copy()

        eligible = [
            dict(statement) for statement in self.statements
            if _iso_date(statement.get('filed_at'), '공시일') <= signal
        ]
        actions = self.corporate_actions.copy()
        if not actions.empty and 'effective_date' in actions:
            dates = actions.effective_date.map(lambda value: _iso_date(value, '기업행사일'))
            actions = actions.loc[dates <= execution].copy()
            actions['effective_date'] = dates.loc[actions.index]
        return {
            'signal_date': signal,
            'execution_date': execution,
            'listing': listing,
            'prices_by_code': histories,
            'eligible_statements': eligible,
            'execution_open': execution_open,
            'valuation_closes': valuation_closes,
            'corporate_actions': actions,
            'issues': issues,
        }

    def fingerprint(self):
        payload = {
            'calendar': self.calendar.date.tolist(),
            'snapshots': {date: _canonical_frame(frame) for date, frame in sorted(self.snapshots.items())},
            'prices': {code: _canonical_frame(frame) for code, frame in sorted(self.prices.items())},
            'statements': sorted(self.statements, key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)),
            'companies': {code: self.companies[code] for code in sorted(self.companies)},
            'indices': {symbol: _canonical_frame(frame) for symbol, frame in sorted(self.indices.items())},
            'corporate_actions': _canonical_frame(self.corporate_actions),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
