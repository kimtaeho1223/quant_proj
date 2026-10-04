"""Transactional SQLite storage for point-in-time investability status."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantdesk.investability import (
    InvestabilityPolicy,
    StatusFinding,
    StatusModel,
    decision_at,
    investability_fingerprint,
)


SCHEMA_VERSION = 1


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


class InvestabilityStore:
    def __init__(self, path):
        self.path = Path(path)
        self.initialize()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS investability_schema (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investability_raw_versions (
                    id INTEGER PRIMARY KEY, dataset TEXT NOT NULL,
                    requested_date TEXT NOT NULL, sha256 TEXT NOT NULL,
                    path TEXT NOT NULL, observed_at TEXT NOT NULL,
                    source_url TEXT NOT NULL, rows_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(dataset,requested_date,sha256)
                );
                CREATE TABLE IF NOT EXISTS investability_source_rows (
                    raw_version_id INTEGER NOT NULL,
                    source_row_number INTEGER NOT NULL,
                    row_json TEXT NOT NULL,
                    PRIMARY KEY(raw_version_id,source_row_number),
                    FOREIGN KEY(raw_version_id) REFERENCES investability_raw_versions(id)
                );
                CREATE TABLE IF NOT EXISTS investability_source_selections (
                    coverage_key TEXT PRIMARY KEY, raw_version_id INTEGER NOT NULL,
                    reason TEXT NOT NULL, selected_at TEXT NOT NULL,
                    FOREIGN KEY(raw_version_id) REFERENCES investability_raw_versions(id)
                );
                CREATE TABLE IF NOT EXISTS investability_policies (
                    policy_id TEXT PRIMARY KEY, version TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investability_model_meta (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, policy_id TEXT NOT NULL,
                    source_selection_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investability_assertions (
                    evidence_id TEXT NOT NULL, security_id TEXT NOT NULL,
                    requested_date TEXT NOT NULL, effective_from TEXT NOT NULL,
                    known_at TEXT, observed_at TEXT NOT NULL, market TEXT NOT NULL,
                    security_kind TEXT NOT NULL, management_designation INTEGER NOT NULL,
                    trading_suspension INTEGER NOT NULL, liquidation_trading INTEGER NOT NULL,
                    raw_version_id INTEGER NOT NULL, source_row_number INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investability_intervals (
                    evidence_id TEXT NOT NULL, security_id TEXT NOT NULL,
                    effective_from TEXT NOT NULL, effective_to TEXT, known_at TEXT,
                    market TEXT NOT NULL, security_kind TEXT NOT NULL,
                    management_designation INTEGER NOT NULL,
                    trading_suspension INTEGER NOT NULL, liquidation_trading INTEGER NOT NULL,
                    raw_version_id INTEGER NOT NULL, source_row_number INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investability_listings (
                    requested_date TEXT NOT NULL, security_id TEXT NOT NULL,
                    issuer_id TEXT, standard_code TEXT, short_code TEXT NOT NULL,
                    name TEXT NOT NULL, market TEXT NOT NULL, security_kind TEXT NOT NULL,
                    valid_from TEXT NOT NULL, valid_to TEXT,
                    raw_version_id INTEGER, source_row_number INTEGER
                );
                CREATE TABLE IF NOT EXISTS investability_sessions (
                    date TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS investability_coverage (
                    date TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS investability_findings (
                    severity TEXT NOT NULL, rule_id TEXT NOT NULL, message TEXT NOT NULL,
                    security_id TEXT, requested_date TEXT, evidence_id TEXT
                );
                CREATE TABLE IF NOT EXISTS investability_reconciliation_runs (
                    id INTEGER PRIMARY KEY, start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL, status TEXT NOT NULL,
                    source_model_fingerprint TEXT NOT NULL,
                    lifecycle_fingerprint TEXT NOT NULL,
                    market_raw_version TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    summary_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investability_reconciliation_findings (
                    run_id INTEGER NOT NULL, severity TEXT NOT NULL,
                    rule_id TEXT NOT NULL, message TEXT NOT NULL,
                    security_id TEXT, requested_date TEXT, evidence_id TEXT,
                    FOREIGN KEY(run_id) REFERENCES investability_reconciliation_runs(id)
                );
                CREATE TABLE IF NOT EXISTS investability_reconciliation_dates (
                    run_id INTEGER NOT NULL, date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(run_id,date),
                    FOREIGN KEY(run_id) REFERENCES investability_reconciliation_runs(id)
                );
                CREATE TABLE IF NOT EXISTS investability_readiness_summaries (
                    id INTEGER PRIMARY KEY, start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL, policy_id TEXT NOT NULL,
                    model_fingerprint TEXT, ready INTEGER NOT NULL,
                    summary_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investability_rebuilds (
                    id INTEGER PRIMARY KEY, expected_fingerprint TEXT NOT NULL,
                    actual_fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                    message TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investability_manual_reviews (
                    id INTEGER PRIMARY KEY, subject TEXT NOT NULL, decision TEXT NOT NULL,
                    reason TEXT NOT NULL, reviewer TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
            ''')
            row = db.execute('SELECT version FROM investability_schema').fetchone()
            if row is None:
                db.execute('INSERT INTO investability_schema(version) VALUES(?)', (SCHEMA_VERSION,))
            elif row[0] != SCHEMA_VERSION:
                raise RuntimeError('지원하지 않는 투자 가능성 스키마 버전입니다.')

    @staticmethod
    def _records(frame):
        if frame is None or frame.empty:
            return []
        return frame.where(pd.notna(frame), None).to_dict(orient='records')

    def save_raw_version(self, artifact, rows):
        records = self._records(rows)
        with self.connect() as db:
            existing = db.execute('''SELECT id FROM investability_raw_versions
                WHERE dataset=? AND requested_date=? AND sha256=?''', (
                artifact.dataset, artifact.requested_date, artifact.sha256,
            )).fetchone()
            if existing:
                return int(existing[0])
            cursor = db.execute('''INSERT INTO investability_raw_versions
                (dataset,requested_date,sha256,path,observed_at,source_url,rows_json,created_at)
                VALUES(?,?,?,?,?,?,?,?)''', (
                artifact.dataset, artifact.requested_date, artifact.sha256,
                str(artifact.path), artifact.observed_at, artifact.source_url,
                _json(records), _now(),
            ))
            raw_version_id = int(cursor.lastrowid)
            db.executemany('''INSERT INTO investability_source_rows
                (raw_version_id,source_row_number,row_json) VALUES(?,?,?)''', [
                (raw_version_id, index, _json(record))
                for index, record in enumerate(records, start=1)
            ])
            return raw_version_id

    def raw_versions(self):
        with self.connect() as db:
            return pd.read_sql_query('''SELECT id,dataset,requested_date,sha256,path,
                observed_at,source_url,created_at FROM investability_raw_versions
                ORDER BY id''', db)

    def raw_version(self, raw_version_id: int):
        with self.connect() as db:
            row = db.execute('''SELECT id,dataset,requested_date,sha256,path,
                observed_at,source_url,created_at FROM investability_raw_versions
                WHERE id=?''', (int(raw_version_id),)).fetchone()
        if row is None:
            return None
        columns = [
            'id', 'dataset', 'requested_date', 'sha256', 'path',
            'observed_at', 'source_url', 'created_at',
        ]
        return dict(zip(columns, row))

    def save_source_rows(self, raw_version_id: int, rows: pd.DataFrame):
        records = self._records(rows)
        with self.connect() as db:
            exists = db.execute(
                'SELECT 1 FROM investability_raw_versions WHERE id=?',
                (int(raw_version_id),),
            ).fetchone()
            if exists is None:
                raise ValueError('존재하지 않는 투자 가능성 원본 버전입니다.')
            db.executemany('''INSERT OR IGNORE INTO investability_source_rows
                (raw_version_id,source_row_number,row_json) VALUES(?,?,?)''', [
                (
                    int(raw_version_id),
                    int(record.get('source_row_number') or index),
                    _json(record),
                )
                for index, record in enumerate(records, start=1)
            ])

    def source_selections(self):
        with self.connect() as db:
            rows = db.execute('''SELECT coverage_key,raw_version_id,reason,selected_at
                FROM investability_source_selections ORDER BY coverage_key''').fetchall()
        return {
            coverage_key: {
                'raw_version_id': int(raw_version_id),
                'reason': reason,
                'selected_at': selected_at,
            }
            for coverage_key, raw_version_id, reason, selected_at in rows
        }

    def set_source_selection(self, coverage_key: str, raw_version_id: int, reason: str):
        day = pd.Timestamp(coverage_key).date().isoformat()
        reason = str(reason).strip()
        if not reason:
            raise ValueError('원본 선택 사유가 필요합니다.')
        with self.connect() as db:
            raw = db.execute(
                'SELECT requested_date FROM investability_raw_versions WHERE id=?',
                (int(raw_version_id),),
            ).fetchone()
            if raw is None:
                raise ValueError('존재하지 않는 투자 가능성 원본 버전입니다.')
            if raw[0] != day:
                raise ValueError('원본 버전의 기준일이 선택 범위와 다릅니다.')
            db.execute('''INSERT INTO investability_source_selections
                (coverage_key,raw_version_id,reason,selected_at) VALUES(?,?,?,?)
                ON CONFLICT(coverage_key) DO UPDATE SET
                    raw_version_id=excluded.raw_version_id,
                    reason=excluded.reason,
                    selected_at=excluded.selected_at''', (
                day, int(raw_version_id), reason, _now(),
            ))

    @staticmethod
    def _insert_records(db, table, records, columns):
        if not records:
            return
        placeholders = ','.join('?' for _ in columns)
        names = ','.join(columns)
        values = [tuple(record.get(column) for column in columns) for record in records]
        db.executemany(f'INSERT INTO {table} ({names}) VALUES ({placeholders})', values)

    def promote(self, model: StatusModel, policy: InvestabilityPolicy,
                source_selection: dict[str, int]) -> str:
        required = {'evidence_id', 'security_id', 'requested_date', 'effective_from'}
        missing = required - set(model.assertions.columns)
        if missing:
            raise ValueError(f'상태 모델 필수 열이 없습니다: {", ".join(sorted(missing))}')
        fingerprint = investability_fingerprint(model, policy)
        with self.connect() as db:
            previous = db.execute(
                'SELECT revision FROM investability_model_meta WHERE id=1'
            ).fetchone()
            revision = int(previous[0]) + 1 if previous else 1
            db.execute('''INSERT OR REPLACE INTO investability_policies
                (policy_id,version,fingerprint,policy_json) VALUES(?,?,?,?)''', (
                policy.policy_id, policy.version, policy.fingerprint, _json(asdict(policy)),
            ))
            for table in (
                'investability_assertions', 'investability_intervals',
                'investability_listings', 'investability_sessions',
                'investability_coverage', 'investability_findings',
            ):
                db.execute(f'DELETE FROM {table}')
            assertion_columns = [
                'evidence_id', 'security_id', 'requested_date', 'effective_from',
                'known_at', 'observed_at', 'market', 'security_kind',
                'management_designation', 'trading_suspension', 'liquidation_trading',
                'raw_version_id', 'source_row_number',
            ]
            assertions = self._records(model.assertions)
            for record in assertions:
                for flag in ('management_designation', 'trading_suspension', 'liquidation_trading'):
                    record[flag] = int(bool(record[flag]))
            self._insert_records(db, 'investability_assertions', assertions, assertion_columns)
            interval_columns = [
                'evidence_id', 'security_id', 'effective_from', 'effective_to',
                'known_at', 'market', 'security_kind', 'management_designation',
                'trading_suspension', 'liquidation_trading', 'raw_version_id',
                'source_row_number',
            ]
            intervals = self._records(model.intervals)
            for record in intervals:
                for flag in ('management_designation', 'trading_suspension', 'liquidation_trading'):
                    record[flag] = int(bool(record[flag]))
            self._insert_records(db, 'investability_intervals', intervals, interval_columns)
            listing_columns = [
                'requested_date', 'security_id', 'issuer_id', 'standard_code',
                'short_code', 'name', 'market', 'security_kind', 'valid_from',
                'valid_to', 'raw_version_id', 'source_row_number',
            ]
            listings = self._records(model.listings)
            self._insert_records(db, 'investability_listings', listings, listing_columns)
            db.executemany(
                'INSERT INTO investability_sessions(date) VALUES(?)',
                [(item,) for item in model.sessions],
            )
            db.executemany(
                'INSERT INTO investability_coverage(date) VALUES(?)',
                [(item,) for item in model.coverage_dates],
            )
            finding_columns = [
                'severity', 'rule_id', 'message', 'security_id',
                'requested_date', 'evidence_id',
            ]
            self._insert_records(
                db, 'investability_findings',
                [asdict(item) for item in model.findings], finding_columns,
            )
            db.execute('''INSERT OR REPLACE INTO investability_model_meta
                (id,revision,fingerprint,policy_id,source_selection_json,created_at)
                VALUES(1,?,?,?,?,?)''', (
                revision, fingerprint, policy.policy_id,
                _json(dict(source_selection)), _now(),
            ))
        return fingerprint

    def current_fingerprint(self):
        with self.connect() as db:
            row = db.execute('SELECT fingerprint FROM investability_model_meta WHERE id=1').fetchone()
        return row[0] if row else None

    def _policy(self, policy_id):
        with self.connect() as db:
            row = db.execute(
                'SELECT policy_json FROM investability_policies WHERE policy_id=?',
                (policy_id,),
            ).fetchone()
        if not row:
            return None
        values = json.loads(row[0])
        for key in ('allowed_markets', 'allowed_security_kinds', 'blocking_flags'):
            values[key] = tuple(values[key])
        return InvestabilityPolicy(**values)

    def _model(self):
        with self.connect() as db:
            assertions = pd.read_sql_query('SELECT * FROM investability_assertions', db)
            intervals = pd.read_sql_query('SELECT * FROM investability_intervals', db)
            listings = pd.read_sql_query('SELECT * FROM investability_listings', db)
            sessions = tuple(row[0] for row in db.execute(
                'SELECT date FROM investability_sessions ORDER BY date'
            ))
            coverage = tuple(row[0] for row in db.execute(
                'SELECT date FROM investability_coverage ORDER BY date'
            ))
            finding_rows = db.execute('''SELECT severity,rule_id,message,security_id,
                requested_date,evidence_id FROM investability_findings''').fetchall()
        for frame in (assertions, intervals):
            for flag in ('management_designation', 'trading_suspension', 'liquidation_trading'):
                if flag in frame:
                    frame[flag] = frame[flag].astype(bool)
        findings = tuple(StatusFinding(*row) for row in finding_rows)
        return StatusModel(assertions, intervals, listings, sessions, coverage, findings)

    def decision(self, security_id: str, as_of_timestamp: str, policy_id: str) -> dict:
        policy = self._policy(policy_id)
        if policy is None:
            return {
                'security_id': security_id, 'as_of_timestamp': as_of_timestamp,
                'state': 'unknown', 'reason_codes': ['unknown_policy'],
                'evidence_ids': [], 'policy_fingerprint': None,
                'model_fingerprint': self.current_fingerprint(),
            }
        return asdict(decision_at(self._model(), security_id, as_of_timestamp, policy))

    def universe(self, as_of_timestamp: str, policy_id: str) -> pd.DataFrame:
        day = pd.Timestamp(as_of_timestamp).date().isoformat()
        model = self._model()
        listed = model.listings[model.listings.requested_date.eq(day)]
        records = []
        for security_id in sorted(listed.security_id.unique() if not listed.empty else []):
            decision = self.decision(security_id, as_of_timestamp, policy_id)
            if decision['state'] == 'eligible':
                identity = listed[listed.security_id.eq(security_id)].iloc[-1].to_dict()
                records.append({**identity, **decision})
        return pd.DataFrame(records)

    def execution_status(self, security_id: str, session_date: str,
                         decision_timestamp: str, policy_id: str) -> dict:
        day = pd.Timestamp(session_date).date().isoformat()
        stamp_day = pd.Timestamp(decision_timestamp).date().isoformat()
        if day != stamp_day:
            return {
                'security_id': security_id, 'as_of_timestamp': decision_timestamp,
                'state': 'unknown', 'reason_codes': ['execution_date_mismatch'],
                'evidence_ids': [], 'policy_fingerprint': None,
                'model_fingerprint': self.current_fingerprint(),
            }
        return self.decision(security_id, decision_timestamp, policy_id)

    def readiness(self, start_date: str, end_date: str, policy_id: str) -> dict:
        start = pd.Timestamp(start_date).date().isoformat()
        end = pd.Timestamp(end_date).date().isoformat()
        policy = self._policy(policy_id)
        with self.connect() as db:
            sessions = [row[0] for row in db.execute(
                'SELECT date FROM investability_sessions WHERE date BETWEEN ? AND ? ORDER BY date',
                (start, end),
            )]
            coverage = {row[0] for row in db.execute(
                'SELECT date FROM investability_coverage WHERE date BETWEEN ? AND ?',
                (start, end),
            )}
            blocking = db.execute('''SELECT COUNT(*) FROM investability_findings
                WHERE severity IN ('blocking','quarantine')''').fetchone()[0]
            meta = db.execute(
                'SELECT fingerprint FROM investability_model_meta WHERE id=1'
            ).fetchone()
            rebuild = db.execute(
                'SELECT status FROM investability_rebuilds ORDER BY id DESC LIMIT 1'
            ).fetchone()
            pending_revisions = [row[0] for row in db.execute('''
                SELECT raw.requested_date
                FROM investability_raw_versions AS raw
                LEFT JOIN investability_source_selections AS selected
                  ON selected.coverage_key=raw.requested_date
                WHERE raw.requested_date BETWEEN ? AND ?
                GROUP BY raw.requested_date,selected.raw_version_id
                HAVING MAX(raw.id) != selected.raw_version_id
                    OR selected.raw_version_id IS NULL
                ORDER BY raw.requested_date
            ''', (start, end))]
            reconciliation_dates = db.execute('''
                SELECT dates.date,dates.status
                FROM investability_reconciliation_dates AS dates
                JOIN (
                    SELECT date,MAX(run_id) AS run_id
                    FROM investability_reconciliation_dates
                    WHERE date BETWEEN ? AND ? GROUP BY date
                ) AS latest
                  ON latest.date=dates.date AND latest.run_id=dates.run_id
                ORDER BY dates.date
            ''', (start, end)).fetchall()
        unknown_dates = sorted(set(sessions) - coverage)
        reconciliation_blocked_dates = [
            day for day, status in reconciliation_dates if status != 'passed'
        ]
        reasons = []
        codes = []
        if policy is None:
            reasons.append('알 수 없는 투자 가능성 정책입니다.')
            codes.append('unknown_policy')
        if meta is None:
            reasons.append('승격된 투자 가능성 모델이 없습니다.')
            codes.append('missing_promoted_model')
        if unknown_dates:
            reasons.append(f'공식 상태가 없는 날짜 {len(unknown_dates)}일이 있습니다.')
            codes.append('missing_daily_coverage')
        if blocking:
            reasons.append(f'해결되지 않은 차단 항목 {blocking}건이 있습니다.')
            codes.append('blocking_findings')
        if pending_revisions:
            reasons.append(f'선택되지 않은 공식 수정본이 {len(pending_revisions)}일 있습니다.')
            codes.append('unselected_revision')
        if rebuild and rebuild[0] == 'failed':
            reasons.append('최근 재빌드 검증 실패가 해결되지 않았습니다.')
            codes.append('rebuild_mismatch')
        if reconciliation_blocked_dates:
            reasons.append(
                f'생애주기·시장 조정 실패 날짜가 {len(reconciliation_blocked_dates)}일 있습니다.'
            )
            codes.append('reconciliation_blocked')
        return {
            'investability_ready': not reasons,
            'blocking_reasons': reasons,
            'blocking_codes': codes,
            'unknown_dates': unknown_dates,
            'pending_revision_dates': pending_revisions,
            'reconciliation_blocked_dates': reconciliation_blocked_dates,
            'start_date': start,
            'end_date': end,
            'policy_id': policy_id,
            'fingerprint': meta[0] if meta else None,
            'corporate_action_ready': False,
            'official_backtest_ready': False,
        }

    def record_reconciliation(self, start_date, end_date, results, findings,
                              source_model_fingerprint, lifecycle_fingerprint,
                              market_raw_version, policy_fingerprint):
        start = pd.Timestamp(start_date).date().isoformat()
        end = pd.Timestamp(end_date).date().isoformat()
        blocking_by_date = {
            item.requested_date for item in findings
            if item.requested_date and item.severity in {'blocking', 'quarantine'}
        }
        result_dates = set(results.requested_date) if not results.empty else set()
        unknown_by_date = set(
            results.loc[results.state.eq('unknown'), 'requested_date']
        ) if not results.empty else set()
        dates = sorted(result_dates | {
            item.requested_date for item in findings if item.requested_date
        })
        date_rows = []
        for day in dates:
            if day in blocking_by_date:
                status = 'blocked'
            elif day in unknown_by_date:
                status = 'unknown'
            else:
                status = 'passed'
            date_rows.append((day, status))
        run_status = 'failed' if any(status != 'passed' for _, status in date_rows) else 'passed'
        summary = {
            'result_rows': int(len(results)),
            'finding_count': len(findings),
            'blocked_dates': [day for day, status in date_rows if status != 'passed'],
        }
        with self.connect() as db:
            cursor = db.execute('''INSERT INTO investability_reconciliation_runs
                (start_date,end_date,status,source_model_fingerprint,
                 lifecycle_fingerprint,market_raw_version,policy_fingerprint,
                 summary_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)''', (
                start, end, run_status, str(source_model_fingerprint),
                str(lifecycle_fingerprint), str(market_raw_version),
                str(policy_fingerprint), _json(summary), _now(),
            ))
            run_id = int(cursor.lastrowid)
            self._insert_records(
                db, 'investability_reconciliation_findings',
                [dict(run_id=run_id, **asdict(item)) for item in findings],
                [
                    'run_id', 'severity', 'rule_id', 'message', 'security_id',
                    'requested_date', 'evidence_id',
                ],
            )
            db.executemany('''INSERT INTO investability_reconciliation_dates
                (run_id,date,status) VALUES(?,?,?)''', [
                (run_id, day, status) for day, status in date_rows
            ])
        return run_id

    def record_rebuild(self, expected_fingerprint, actual_fingerprint, status, message):
        with self.connect() as db:
            cursor = db.execute('''INSERT INTO investability_rebuilds
                (expected_fingerprint,actual_fingerprint,status,message,created_at)
                VALUES(?,?,?,?,?)''', (
                expected_fingerprint, actual_fingerprint, status, str(message), _now(),
            ))
            return int(cursor.lastrowid)

    def latest_rebuild(self):
        with self.connect() as db:
            row = db.execute('''SELECT id,expected_fingerprint,actual_fingerprint,
                status,message,created_at FROM investability_rebuilds
                ORDER BY id DESC LIMIT 1''').fetchone()
        if not row:
            return None
        columns = ['id', 'expected_fingerprint', 'actual_fingerprint', 'status', 'message', 'created_at']
        return dict(zip(columns, row))

    def record_manual_review(self, subject, decision, reason, reviewer, evidence_ids):
        if not str(reason).strip():
            raise ValueError('수동 검토 사유가 필요합니다.')
        with self.connect() as db:
            cursor = db.execute('''INSERT INTO investability_manual_reviews
                (subject,decision,reason,reviewer,evidence_ids_json,created_at)
                VALUES(?,?,?,?,?,?)''', (
                str(subject), str(decision), str(reason), str(reviewer),
                _json(list(evidence_ids)), _now(),
            ))
            return int(cursor.lastrowid)
