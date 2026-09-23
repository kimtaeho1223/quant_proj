"""Atomic SQLite persistence for reproducible backtest runs."""
import json
from datetime import datetime, timezone

import pandas as pd

from quantdesk.storage import Store


def _json_ready(value):
    if isinstance(value, pd.DataFrame):
        return {
            '__dataframe__': [_json_ready(row) for row in value.to_dict('records')],
            'columns': value.columns.tolist(),
        }
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, 'item'):
        return _json_ready(value.item())
    raise TypeError(f'저장할 수 없는 값입니다: {type(value).__name__}')


def _restore(value):
    if isinstance(value, dict) and '__dataframe__' in value:
        return pd.DataFrame(value['__dataframe__'], columns=value['columns'])
    if isinstance(value, dict):
        return {key: _restore(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore(item) for item in value]
    return value


def _dump(value):
    return json.dumps(_json_ready(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class BacktestStore(Store):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY, created TEXT, status TEXT, fingerprint TEXT,
                config TEXT, issues TEXT, audit TEXT, extras TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS backtest_nav (
                run_id INTEGER, seq INTEGER, payload TEXT, PRIMARY KEY(run_id,seq))''')
            db.execute('''CREATE TABLE IF NOT EXISTS backtest_weekly (
                run_id INTEGER, seq INTEGER, signal_date TEXT, execution_date TEXT,
                payload TEXT, PRIMARY KEY(run_id,seq))''')
            db.execute('''CREATE TABLE IF NOT EXISTS backtest_trades (
                run_id INTEGER, seq INTEGER, signal_date TEXT, execution_date TEXT,
                code TEXT, side TEXT, payload TEXT, PRIMARY KEY(run_id,seq))''')

    def save_run(self, result):
        required = {'status', 'fingerprint', 'config', 'issues', 'audit', 'nav', 'weekly', 'trades'}
        if not required.issubset(result):
            raise ValueError('백테스트 결과 필수 항목이 없습니다.')
        nav = result['nav'].to_dict('records')
        weekly = result['weekly'].to_dict('records')
        trades = result['trades'].to_dict('records')
        extras = {key: value for key, value in result.items() if key not in required}
        config_json = _dump(result['config'])
        issues_json = _dump(result['issues'])
        audit_json = _dump(result['audit'])
        extras_json = _dump(extras)
        nav_json = [_dump(row) for row in nav]
        weekly_json = [_dump(row) for row in weekly]
        trades_json = [_dump(row) for row in trades]

        with self.connect() as db:
            cursor = db.execute('''INSERT INTO backtest_runs
                (created,status,fingerprint,config,issues,audit,extras) VALUES(?,?,?,?,?,?,?)''',
                (datetime.now(timezone.utc).isoformat(), result['status'], result['fingerprint'],
                 config_json, issues_json, audit_json, extras_json))
            run_id = cursor.lastrowid
            db.executemany('INSERT INTO backtest_nav VALUES(?,?,?)',
                           [(run_id, seq, payload) for seq, payload in enumerate(nav_json)])
            db.executemany('INSERT INTO backtest_weekly VALUES(?,?,?,?,?)', [
                (run_id, seq, str(row.get('signal_date', '')), str(row.get('execution_date', '')), weekly_json[seq])
                for seq, row in enumerate(weekly)
            ])
            db.executemany('INSERT INTO backtest_trades VALUES(?,?,?,?,?,?,?)', [
                (run_id, seq, str(row.get('signal_date', '')), str(row.get('execution_date', '')),
                 str(row.get('code', '')), str(row.get('side', '')), trades_json[seq])
                for seq, row in enumerate(trades)
            ])
        return int(run_id)

    def load_run(self, run_id):
        with self.connect() as db:
            row = db.execute('''SELECT status,fingerprint,config,issues,audit,extras
                FROM backtest_runs WHERE id=?''', (run_id,)).fetchone()
            if row is None:
                raise ValueError('백테스트 실행 기록이 없습니다.')
            nav = [json.loads(item[0]) for item in db.execute(
                'SELECT payload FROM backtest_nav WHERE run_id=? ORDER BY seq', (run_id,)).fetchall()]
            weekly = [json.loads(item[0]) for item in db.execute(
                'SELECT payload FROM backtest_weekly WHERE run_id=? ORDER BY seq', (run_id,)).fetchall()]
            trades = [json.loads(item[0]) for item in db.execute(
                'SELECT payload FROM backtest_trades WHERE run_id=? ORDER BY seq', (run_id,)).fetchall()]
        result = {
            'id': int(run_id), 'status': row[0], 'fingerprint': row[1],
            'config': _restore(json.loads(row[2])),
            'issues': _restore(json.loads(row[3])),
            'audit': _restore(json.loads(row[4])),
            'nav': pd.DataFrame(nav), 'weekly': pd.DataFrame(weekly),
            'trades': pd.DataFrame(trades),
        }
        result.update(_restore(json.loads(row[5])))
        return result

    def runs(self):
        with self.connect() as db:
            return pd.read_sql_query('''SELECT id,created,status,fingerprint
                FROM backtest_runs ORDER BY id DESC''', db)
