"""Validated market-data cache, independent of the demo portfolio."""
import io
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from quantdesk.storage import Store


def market_values_complete(frame):
    required = {'Marcap', 'Amount'}
    if not required.issubset(frame.columns) or frame.empty:
        return False
    values = frame[['Marcap', 'Amount']].apply(pd.to_numeric, errors='coerce')
    valid = values.Marcap.gt(0) & values.Amount.gt(0)
    return int(valid.sum()) >= max(1, int(len(frame) * 0.95))


def provider_read(kind, *args):
    try:
        process = subprocess.run([sys.executable, '-m', 'quantdesk.market_worker', kind, *args],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, encoding='utf-8', errors='replace', timeout=35)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('제공처 응답이 35초를 초과했습니다. 다시 갱신해 주세요.') from exc
    if process.returncode:
        raise ValueError('제공처 연결 또는 응답 오류입니다. 네트워크와 제공처 상태를 확인해 주세요.')
    try:
        return pd.read_json(io.StringIO(process.stdout), orient='table')
    except (ValueError, KeyError) as exc:
        raise ValueError('제공처 응답 형식을 읽을 수 없습니다.') from exc


def normalize_prices(frame, end):
    fields = ['Open', 'High', 'Low', 'Close', 'Volume']
    if frame.empty or not {'Date', *fields}.issubset(frame.columns):
        raise ValueError('일별 주가 데이터가 비어 있거나 필수 항목이 없습니다.')
    data = frame[['Date', *fields]].copy()
    data['Date'] = pd.to_datetime(data.Date, errors='coerce')
    data[fields] = data[fields].apply(pd.to_numeric, errors='coerce')
    data = data.replace([float('inf'), -float('inf')], float('nan'))
    if data.isna().any().any() or (data.Close <= 0).any() or (data[fields] < 0).any().any():
        raise ValueError('유효하지 않은 날짜 또는 가격·거래량이 있습니다.')
    data = data[data.Date <= pd.Timestamp(end)].sort_values('Date').drop_duplicates('Date', keep='last')
    if data.empty:
        raise ValueError('요청 기간에 주가 데이터가 없습니다.')
    data['Date'] = data.Date.dt.strftime('%Y-%m-%d')
    return data


class MarketStore(Store):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS market_listing (id INTEGER PRIMARY KEY, payload TEXT, fetched TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS market_prices (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(code,date))')
            db.execute('CREATE TABLE IF NOT EXISTS market_logs (id INTEGER PRIMARY KEY, code TEXT, status TEXT, message TEXT, fetched TEXT)')
            db.execute('''CREATE TABLE IF NOT EXISTS market_snapshots (
                effective_date TEXT, code TEXT, name TEXT, market TEXT, security_type TEXT,
                marcap REAL, amount REAL, PRIMARY KEY(effective_date,code))''')
            db.execute('''CREATE TABLE IF NOT EXISTS market_indices (
                symbol TEXT, date TEXT, open REAL, close REAL, PRIMARY KEY(symbol,date))''')
            db.execute('''CREATE TABLE IF NOT EXISTS corporate_actions (
                code TEXT, effective_date TEXT, action_type TEXT, ratio REAL,
                cash_amount REAL, status TEXT, source TEXT,
                PRIMARY KEY(code,effective_date,action_type))''')

    def record(self, code, status, message):
        with self.connect() as db:
            db.execute('INSERT INTO market_logs(code,status,message,fetched) VALUES (?,?,?,?)',
                       (code, status, message, datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')))

    def latest_results(self):
        with self.connect() as db:
            return pd.read_sql_query('SELECT code AS 종목코드, status AS 상태, message AS 내용, fetched AS 수집시각 FROM market_logs WHERE id IN (SELECT MAX(id) FROM market_logs GROUP BY code) ORDER BY id DESC', db)

    def logs(self):
        with self.connect() as db:
            return pd.read_sql_query('SELECT code AS 종목코드, status AS 상태, message AS 내용, fetched AS 수집시각 FROM market_logs ORDER BY id DESC LIMIT 500', db)

    def listing(self):
        with self.connect() as db:
            row = db.execute('SELECT payload, fetched FROM market_listing WHERE id=1').fetchone()
        return (pd.read_json(io.StringIO(row[0]), orient='table'), row[1]) if row else (pd.DataFrame(), None)

    def save_listing(self, frame):
        if frame.empty or not {'Code', 'Name', 'Market', 'Marcap', 'Amount'}.issubset(frame.columns):
            raise ValueError('종목 목록에 필수 항목이 없습니다. 이전 목록을 유지합니다.')
        frame = frame[frame.Market.isin(['KOSPI', 'KOSDAQ'])].copy()
        frame['Code'] = frame.Code.astype(str).str.zfill(6)
        frame[['Marcap', 'Amount']] = frame[['Marcap', 'Amount']].apply(pd.to_numeric, errors='coerce')
        if frame.empty or not frame.Code.str.fullmatch(r'[0-9A-Z]{6}').all() or frame.Code.duplicated().any():
            raise ValueError('종목 목록의 코드가 유효하지 않습니다.')
        if not market_values_complete(frame):
            raise ValueError('시가총액 또는 거래대금 데이터가 충분하지 않습니다. 이전 목록을 유지합니다.')
        now = datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO market_listing VALUES(1,?,?)', (frame.to_json(orient='table'), now))

    def save_prices(self, code, data):
        rows = [(code, *row) for row in data.itertuples(index=False, name=None)]
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO market_prices VALUES(?,?,?,?,?,?,?)', rows)

    def prices(self, code):
        with self.connect() as db:
            return pd.read_sql_query('SELECT date AS Date, open AS Open, high AS High, low AS Low, close AS Close, volume AS Volume FROM market_prices WHERE code=? ORDER BY date', db, params=(code,))

    def summary(self):
        with self.connect() as db:
            return pd.read_sql_query('SELECT code AS Code, MIN(date) AS 시작일, MAX(date) AS 마지막거래일, COUNT(*) AS 저장일수 FROM market_prices GROUP BY code', db)

    def save_historical_snapshot(self, effective_date, frame):
        date = pd.Timestamp(effective_date).date().isoformat()
        required = {'Code', 'Name', 'Market', 'SecurityType', 'Marcap', 'Amount'}
        if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
            raise ValueError('과거 종목군 필수 열이 없습니다.')
        data = frame[list(required)].copy()
        data['Code'] = data.Code.astype(str).str.zfill(6)
        data[['Marcap', 'Amount']] = data[['Marcap', 'Amount']].apply(pd.to_numeric, errors='coerce')
        if data.empty or data.Code.duplicated().any() or data[['Marcap', 'Amount']].isna().any().any():
            raise ValueError('과거 종목군 코드 또는 시장값이 유효하지 않습니다.')
        if (data[['Marcap', 'Amount']] <= 0).any().any():
            raise ValueError('과거 종목군 시가총액과 거래대금은 양수여야 합니다.')
        rows = [
            (date, row.Code, row.Name, row.Market, row.SecurityType, float(row.Marcap), float(row.Amount))
            for row in data.itertuples(index=False)
        ]
        with self.connect() as db:
            db.execute('DELETE FROM market_snapshots WHERE effective_date=?', (date,))
            db.executemany('INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?)', rows)

    def historical_snapshot(self, effective_date):
        date = pd.Timestamp(effective_date).date().isoformat()
        with self.connect() as db:
            return pd.read_sql_query('''SELECT effective_date, code AS Code, name AS Name,
                market AS Market, security_type AS SecurityType, marcap AS Marcap, amount AS Amount
                FROM market_snapshots WHERE effective_date=? ORDER BY marcap DESC, code''', db, params=(date,))

    def save_index_prices(self, symbol, frame):
        if not symbol or not isinstance(frame, pd.DataFrame) or not {'Date', 'Open', 'Close'}.issubset(frame.columns):
            raise ValueError('지수 가격 필수 값을 확인해 주세요.')
        data = frame[['Date', 'Open', 'Close']].copy()
        data['Date'] = pd.to_datetime(data.Date, errors='coerce').dt.strftime('%Y-%m-%d')
        data[['Open', 'Close']] = data[['Open', 'Close']].apply(pd.to_numeric, errors='coerce')
        if data.empty or data.isna().any().any() or data.Date.duplicated().any() or (data[['Open', 'Close']] <= 0).any().any():
            raise ValueError('지수 가격이 유효하지 않습니다.')
        rows = [(str(symbol), row.Date, float(row.Open), float(row.Close)) for row in data.itertuples(index=False)]
        with self.connect() as db:
            db.execute('DELETE FROM market_indices WHERE symbol=?', (str(symbol),))
            db.executemany('INSERT INTO market_indices VALUES(?,?,?,?)', rows)

    def index_prices(self, symbol):
        with self.connect() as db:
            return pd.read_sql_query('''SELECT date AS Date, open AS Open, close AS Close
                FROM market_indices WHERE symbol=? ORDER BY date''', db, params=(str(symbol),))

    def save_corporate_actions(self, frame):
        columns = ['code', 'effective_date', 'action_type', 'ratio', 'cash_amount', 'status', 'source']
        if not isinstance(frame, pd.DataFrame) or not set(columns).issubset(frame.columns):
            raise ValueError('기업행사 필수 열이 없습니다.')
        data = frame[columns].copy()
        data['code'] = data.code.astype(str).str.zfill(6)
        data['effective_date'] = pd.to_datetime(data.effective_date, errors='coerce').dt.strftime('%Y-%m-%d')
        data[['ratio', 'cash_amount']] = data[['ratio', 'cash_amount']].apply(pd.to_numeric, errors='coerce')
        if data[['code', 'effective_date', 'action_type', 'status', 'source']].isna().any().any():
            raise ValueError('기업행사 값이 유효하지 않습니다.')
        rows = [tuple(row) for row in data.itertuples(index=False, name=None)]
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO corporate_actions VALUES(?,?,?,?,?,?,?)', rows)

    def corporate_actions(self, start, end):
        first = pd.Timestamp(start).date().isoformat()
        last = pd.Timestamp(end).date().isoformat()
        with self.connect() as db:
            return pd.read_sql_query('''SELECT code, effective_date, action_type, ratio,
                cash_amount, status, source FROM corporate_actions
                WHERE effective_date BETWEEN ? AND ? ORDER BY effective_date,code,action_type''',
                db, params=(first, last))


def refresh_listing(store):
    try:
        store.save_listing(provider_read('listing'))
        store.record('목록', '성공', 'KOSPI·KOSDAQ 종목 목록 저장')
        return True
    except ValueError as exc:
        store.record('목록', '실패', str(exc))
        return False


def refresh_prices(store, codes, start, end, provider=None, progress=None):
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError('시작일은 종료일보다 늦을 수 없습니다.')
    provider = provider or (lambda code, first, last: provider_read('prices', code, first, last))
    results = []
    for index, code in enumerate(dict.fromkeys(codes)):
        try:
            # Refresh the full requested window to avoid mixing corporate-action adjustment bases.
            data = normalize_prices(provider(code, start, end), end)
            data = data[data.Date >= start]
            if data.empty:
                raise ValueError('요청 기간에 주가가 없습니다.')
            store.save_prices(code, data)
            status, message = '성공', f'{len(data)}일 저장 · 마지막 거래일 {data.Date.iloc[-1]}'
        except (ValueError, OSError) as exc:
            status, message = '실패', str(exc)
        store.record(code, status, message)
        results.append(dict(code=code, status=status, message=message))
        if progress:
            progress((index + 1) / len(codes))
    return results


def refresh_top_prices(store, listing, start, end, provider=None, progress=None):
    """Refresh daily prices for the same top-100 universe used by the ranker."""
    from quantdesk.real_ranking import candidate_universe
    candidates = candidate_universe(listing)
    if candidates.empty:
        raise ValueError('상위 100개 후보 종목을 만들 수 없습니다. 종목 목록을 먼저 갱신해 주세요.')
    return refresh_prices(store, candidates.Code.tolist(), start, end, provider=provider, progress=progress)
