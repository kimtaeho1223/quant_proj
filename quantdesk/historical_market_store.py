"""SQLite persistence for historical full-market acquisition."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantdesk.historical_market import DateState, transition_date_state


class HistoricalMarketStore:
    SCHEMA_VERSION = 1

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS historical_schema (
                    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO historical_schema VALUES(1, 1);
                CREATE TABLE IF NOT EXISTS historical_jobs (
                    id INTEGER PRIMARY KEY, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    source_mode TEXT NOT NULL, state TEXT NOT NULL, pause_requested INTEGER NOT NULL DEFAULT 0,
                    worker_token TEXT, heartbeat TEXT, created TEXT NOT NULL, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS historical_job_dates (
                    job_id INTEGER NOT NULL, trade_date TEXT NOT NULL, state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    selected_raw_id INTEGER, updated TEXT NOT NULL,
                    PRIMARY KEY(job_id, trade_date),
                    FOREIGN KEY(job_id) REFERENCES historical_jobs(id),
                    FOREIGN KEY(selected_raw_id) REFERENCES historical_raw_versions(id)
                );
                CREATE TABLE IF NOT EXISTS historical_raw_versions (
                    id INTEGER PRIMARY KEY, trade_date TEXT NOT NULL, source_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL, path TEXT NOT NULL, fetched_at TEXT NOT NULL,
                    parser_version TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    UNIQUE(trade_date, source_type, sha256)
                );
                CREATE TABLE IF NOT EXISTS historical_findings (
                    id INTEGER PRIMARY KEY, raw_version_id INTEGER NOT NULL,
                    severity TEXT NOT NULL, rule_id TEXT NOT NULL, message TEXT NOT NULL,
                    observed TEXT, threshold_value TEXT, resolution_evidence TEXT,
                    created TEXT NOT NULL,
                    FOREIGN KEY(raw_version_id) REFERENCES historical_raw_versions(id)
                );
                CREATE TABLE IF NOT EXISTS historical_daily_market (
                    trade_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
                    market TEXT NOT NULL, segment TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL NOT NULL,
                    volume REAL NOT NULL, amount REAL NOT NULL, marcap REAL NOT NULL,
                    listed_shares REAL NOT NULL, raw_version_id INTEGER NOT NULL,
                    PRIMARY KEY(trade_date, code),
                    FOREIGN KEY(raw_version_id) REFERENCES historical_raw_versions(id)
                );
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def create_job(self, start_date, end_date, dates, source_mode):
        if start_date > end_date:
            raise ValueError('수집 시작일은 종료일보다 늦을 수 없습니다.')
        unique_dates = list(dict.fromkeys(dates))
        now = self._now()
        with self.connect() as db:
            cursor = db.execute('''INSERT INTO historical_jobs
                (start_date,end_date,source_mode,state,created,updated)
                VALUES(?,?,?,?,?,?)''',
                (start_date, end_date, source_mode, 'active', now, now))
            job_id = cursor.lastrowid
            db.executemany('''INSERT INTO historical_job_dates
                (job_id,trade_date,state,updated) VALUES(?,?,?,?)''',
                [(job_id, day, DateState.PENDING.value, now) for day in unique_dates])
        return job_id

    def transition_date(self, job_id, trade_date, target, message=''):
        target = DateState(target)
        now = self._now()
        with self.connect() as db:
            row = db.execute('''SELECT state,attempts FROM historical_job_dates
                WHERE job_id=? AND trade_date=?''', (job_id, trade_date)).fetchone()
            if not row:
                raise KeyError((job_id, trade_date))
            transition_date_state(DateState(row[0]), target)
            attempts = row[1] + (1 if target == DateState.DOWNLOADING else 0)
            db.execute('''UPDATE historical_job_dates
                SET state=?,attempts=?,message=?,updated=? WHERE job_id=? AND trade_date=?''',
                (target.value, attempts, message, now, job_id, trade_date))
            db.execute('UPDATE historical_jobs SET updated=? WHERE id=?', (now, job_id))
        return target

    def date_state(self, job_id, trade_date):
        with self.connect() as db:
            row = db.execute('''SELECT state FROM historical_job_dates
                WHERE job_id=? AND trade_date=?''', (job_id, trade_date)).fetchone()
        if not row:
            raise KeyError((job_id, trade_date))
        return DateState(row[0])

    def job_dates(self, job_id):
        with self.connect() as db:
            return pd.read_sql_query('''SELECT trade_date,state,attempts,message,selected_raw_id,updated
                FROM historical_job_dates WHERE job_id=? ORDER BY trade_date''',
                db, params=(job_id,))

    def next_processable_date(self, job_id, max_attempts=3):
        with self.connect() as db:
            row = db.execute('''SELECT trade_date FROM historical_job_dates
                WHERE job_id=? AND (
                    state=? OR (state=? AND attempts < ?)
                ) ORDER BY trade_date LIMIT 1''', (
                    job_id, DateState.PENDING.value, DateState.FAILED.value, max_attempts,
                )).fetchone()
        return row[0] if row else None

    def set_pause(self, job_id, paused):
        now = self._now()
        with self.connect() as db:
            cursor = db.execute('''UPDATE historical_jobs SET pause_requested=?,updated=? WHERE id=?''',
                                (int(bool(paused)), now, job_id))
            if cursor.rowcount != 1:
                raise KeyError(job_id)

    def pause_requested(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT pause_requested FROM historical_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        return bool(row[0])

    def record_raw_version(self, archived, metadata=None, parser_version='1'):
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO historical_raw_versions
                (trade_date,source_type,sha256,path,fetched_at,parser_version,metadata_json)
                VALUES(?,?,?,?,?,?,?)''', (
                    archived.requested_date, archived.source_type, archived.sha256,
                    str(archived.path), archived.fetched_at, str(parser_version),
                    json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True),
                ))
            row = db.execute('''SELECT id FROM historical_raw_versions
                WHERE trade_date=? AND source_type=? AND sha256=?''',
                (archived.requested_date, archived.source_type, archived.sha256)).fetchone()
        return row[0]

    def raw_versions(self, trade_date):
        with self.connect() as db:
            return pd.read_sql_query('''SELECT * FROM historical_raw_versions
                WHERE trade_date=? ORDER BY id''', db, params=(trade_date,))

    def prior_summaries(self, trade_date, limit=20):
        with self.connect() as db:
            rows = db.execute('''SELECT trade_date,COUNT(*),SUM(marcap)
                FROM historical_daily_market WHERE trade_date < ?
                GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?''',
                (trade_date, int(limit))).fetchall()
        return [
            {'trade_date': day, 'row_count': count, 'total_marcap': total}
            for day, count, total in reversed(rows)
        ]

    def save_findings(self, raw_version_id, findings):
        now = self._now()
        rows = [(
            raw_version_id, item.severity, item.rule_id, item.message,
            None if item.observed is None else str(item.observed),
            None if item.threshold is None else str(item.threshold), None, now,
        ) for item in findings]
        with self.connect() as db:
            db.execute('DELETE FROM historical_findings WHERE raw_version_id=?', (raw_version_id,))
            if rows:
                db.executemany('''INSERT INTO historical_findings
                    (raw_version_id,severity,rule_id,message,observed,threshold_value,
                     resolution_evidence,created) VALUES(?,?,?,?,?,?,?,?)''', rows)

    def findings(self, raw_version_id):
        with self.connect() as db:
            rows = db.execute('''SELECT severity,rule_id,message,observed,threshold_value,
                resolution_evidence,created FROM historical_findings
                WHERE raw_version_id=? ORDER BY id''', (raw_version_id,)).fetchall()
        keys = ['severity', 'rule_id', 'message', 'observed', 'threshold', 'resolution_evidence', 'created']
        return [dict(zip(keys, row)) for row in rows]

    def promote_day(self, job_id, trade_date, raw_version_id, frame):
        rows = [(
            row.trade_date, row.code, row.name, row.market, row.segment,
            None if pd.isna(row.open) else float(row.open),
            None if pd.isna(row.high) else float(row.high),
            None if pd.isna(row.low) else float(row.low),
            float(row.close), float(row.volume), float(row.amount), float(row.marcap),
            float(row.listed_shares), raw_version_id,
        ) for row in frame.itertuples(index=False)]
        now = self._now()
        with self.connect() as db:
            state = db.execute('''SELECT state FROM historical_job_dates
                WHERE job_id=? AND trade_date=?''', (job_id, trade_date)).fetchone()
            if not state:
                raise KeyError((job_id, trade_date))
            transition_date_state(DateState(state[0]), DateState.PROMOTED)
            db.execute('DELETE FROM historical_daily_market WHERE trade_date=?', (trade_date,))
            db.executemany('''INSERT INTO historical_daily_market VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
            db.execute('''UPDATE historical_job_dates
                SET state=?,selected_raw_id=?,updated=? WHERE job_id=? AND trade_date=?''',
                (DateState.PROMOTED.value, raw_version_id, now, job_id, trade_date))

    def canonical_day(self, trade_date):
        with self.connect() as db:
            return pd.read_sql_query('''SELECT trade_date,code,name,market,segment,
                open,high,low,close,volume,amount,marcap,listed_shares,raw_version_id
                FROM historical_daily_market WHERE trade_date=? ORDER BY market DESC,code''',
                db, params=(trade_date,))

    def job_summary(self, job_id):
        summary = {state.value: 0 for state in DateState}
        with self.connect() as db:
            rows = db.execute('''SELECT state,COUNT(*) FROM historical_job_dates
                WHERE job_id=? GROUP BY state''', (job_id,)).fetchall()
        for state, count in rows:
            summary[state] = count
        summary['total'] = sum(count for _, count in rows)
        return summary

    def acquire_lease(self, job_id, token, now, stale_after):
        now = now.astimezone(timezone.utc)
        with self.connect() as db:
            row = db.execute('SELECT worker_token,heartbeat FROM historical_jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            active_token, heartbeat = row
            fresh = False
            if active_token and heartbeat:
                last = datetime.fromisoformat(heartbeat)
                fresh = now - last <= stale_after
            if fresh and active_token != token:
                return False
            db.execute('''UPDATE historical_jobs SET worker_token=?,heartbeat=?,updated=? WHERE id=?''',
                       (token, now.isoformat(), now.isoformat(), job_id))
        return True

    def heartbeat(self, job_id, token, now=None):
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self.connect() as db:
            cursor = db.execute('''UPDATE historical_jobs SET heartbeat=?,updated=?
                WHERE id=? AND worker_token=?''', (now, now, job_id, token))
            if cursor.rowcount != 1:
                raise ValueError('활성 작업 임대를 확인할 수 없습니다.')

    def release_lease(self, job_id, token):
        with self.connect() as db:
            db.execute('''UPDATE historical_jobs SET worker_token=NULL,heartbeat=NULL
                WHERE id=? AND worker_token=?''', (job_id, token))
