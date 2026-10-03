"""Transactional SQLite storage for official security lifecycles."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from quantdesk.security_lifecycle import lifecycle_fingerprint


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clean(value):
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, 'item') else value


class SecurityLifecycleStore:
    SCHEMA_VERSION = 1

    def __init__(self, path, promotion_hook=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.promotion_hook = promotion_hook
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lifecycle_schema (
                    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO lifecycle_schema VALUES(1, 1);
                CREATE TABLE IF NOT EXISTS lifecycle_raw_versions (
                    id INTEGER PRIMARY KEY, dataset TEXT NOT NULL,
                    scope_start TEXT, scope_end TEXT, sha256 TEXT NOT NULL,
                    path TEXT NOT NULL, fetched_at TEXT NOT NULL,
                    parser_version TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    UNIQUE(dataset,scope_start,scope_end,sha256)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_source_rows (
                    raw_version_id INTEGER NOT NULL, source_row_number INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(raw_version_id,source_row_number),
                    FOREIGN KEY(raw_version_id) REFERENCES lifecycle_raw_versions(id)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_source_selections (
                    dataset TEXT PRIMARY KEY, raw_version_id INTEGER NOT NULL,
                    evidence TEXT NOT NULL, selected_at TEXT NOT NULL,
                    FOREIGN KEY(raw_version_id) REFERENCES lifecycle_raw_versions(id)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_model_meta (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, source_selection_json TEXT NOT NULL,
                    promoted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_issuers (
                    issuer_id TEXT PRIMARY KEY, official_reference TEXT NOT NULL,
                    raw_version_id INTEGER NOT NULL, source_row_number INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_securities (
                    security_id TEXT PRIMARY KEY, issuer_id TEXT,
                    standard_code TEXT NOT NULL, security_kind TEXT NOT NULL,
                    raw_version_id INTEGER NOT NULL, source_row_number INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_identifiers (
                    security_id TEXT NOT NULL, standard_code TEXT NOT NULL,
                    short_code TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
                    raw_version_id INTEGER NOT NULL, source_row_number INTEGER NOT NULL,
                    PRIMARY KEY(security_id,short_code,valid_from)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_names (
                    security_id TEXT NOT NULL, name TEXT NOT NULL,
                    valid_from TEXT NOT NULL, valid_to TEXT,
                    raw_version_id INTEGER NOT NULL, source_row_number INTEGER NOT NULL,
                    PRIMARY KEY(security_id,valid_from)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_events (
                    security_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    effective_date TEXT NOT NULL, last_trading_date TEXT,
                    successor_standard_code TEXT, raw_version_id INTEGER NOT NULL,
                    source_row_number INTEGER NOT NULL,
                    PRIMARY KEY(security_id,event_type,effective_date)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_lineage (
                    predecessor_security_id TEXT NOT NULL, successor_security_id TEXT NOT NULL,
                    relationship_type TEXT NOT NULL, effective_date TEXT NOT NULL,
                    raw_version_id INTEGER NOT NULL, source_row_number INTEGER NOT NULL,
                    PRIMARY KEY(predecessor_security_id,successor_security_id,relationship_type,effective_date)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_intervals (
                    security_id TEXT NOT NULL, issuer_id TEXT, standard_code TEXT NOT NULL,
                    short_code TEXT NOT NULL, name TEXT NOT NULL, market TEXT NOT NULL,
                    security_kind TEXT NOT NULL, raw_version_id INTEGER NOT NULL,
                    source_row_number INTEGER NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
                    PRIMARY KEY(security_id,valid_from)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_findings (
                    id INTEGER PRIMARY KEY, model_revision INTEGER NOT NULL,
                    severity TEXT NOT NULL, rule_id TEXT NOT NULL, message TEXT NOT NULL,
                    security_id TEXT, effective_date TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_rebuilds (
                    id INTEGER PRIMARY KEY, parser_version TEXT NOT NULL,
                    rules_version TEXT NOT NULL, source_selection_json TEXT NOT NULL,
                    expected_fingerprint TEXT, actual_fingerprint TEXT,
                    status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_reconciliation_runs (
                    id INTEGER PRIMARY KEY, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    daily_coverage REAL NOT NULL, unresolved_dates_json TEXT NOT NULL,
                    corporate_action_pending INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_reconciliation_findings (
                    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
                    severity TEXT NOT NULL, rule_id TEXT NOT NULL, message TEXT NOT NULL,
                    security_id TEXT, effective_date TEXT,
                    lifecycle_raw_version_id INTEGER, market_raw_version_id INTEGER,
                    lifecycle_value TEXT, market_value TEXT,
                    FOREIGN KEY(run_id) REFERENCES lifecycle_reconciliation_runs(id)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_manual_reviews (
                    id INTEGER PRIMARY KEY, source_url TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
                    example_type TEXT NOT NULL, expected_event TEXT NOT NULL,
                    derived_interval TEXT NOT NULL, reviewer_result TEXT NOT NULL,
                    notes TEXT NOT NULL, created_at TEXT NOT NULL
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

    def record_raw_version(self, archived, metadata, parser_version):
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO lifecycle_raw_versions
                (dataset,scope_start,scope_end,sha256,path,fetched_at,parser_version,metadata_json)
                VALUES(?,?,?,?,?,?,?,?)''', (
                archived.dataset, archived.scope_start, archived.scope_end,
                archived.sha256, str(archived.path), archived.fetched_at,
                str(parser_version), json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True),
            ))
            row = db.execute('''SELECT id FROM lifecycle_raw_versions
                WHERE dataset=? AND scope_start IS ? AND scope_end IS ? AND sha256=?''', (
                archived.dataset, archived.scope_start, archived.scope_end, archived.sha256,
            )).fetchone()
        return int(row[0])

    def raw_versions(self, dataset=None):
        query = 'SELECT * FROM lifecycle_raw_versions'
        params = ()
        if dataset:
            query += ' WHERE dataset=?'
            params = (dataset,)
        query += ' ORDER BY id'
        with self.connect() as db:
            return pd.read_sql_query(query, db, params=params)

    def save_source_rows(self, raw_version_id, rows):
        records = []
        for index, row in rows.reset_index(drop=True).iterrows():
            payload = {key: _clean(value) for key, value in row.to_dict().items()}
            payload['raw_version_id'] = int(raw_version_id)
            payload['source_row_number'] = int(payload.get('source_row_number') or index + 1)
            records.append((
                int(raw_version_id), payload['source_row_number'],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ))
        with self.connect() as db:
            db.execute('DELETE FROM lifecycle_source_rows WHERE raw_version_id=?', (raw_version_id,))
            db.executemany('INSERT INTO lifecycle_source_rows VALUES(?,?,?)', records)

    def source_rows(self, raw_version_id):
        with self.connect() as db:
            rows = db.execute('''SELECT payload_json FROM lifecycle_source_rows
                WHERE raw_version_id=? ORDER BY source_row_number''', (raw_version_id,)).fetchall()
        return pd.DataFrame([json.loads(row[0]) for row in rows])

    def select_raw_version(self, dataset, raw_version_id, evidence):
        evidence = str(evidence).strip()
        if not evidence:
            raise ValueError('원본 선택에는 공식 근거가 필요합니다.')
        with self.connect() as db:
            row = db.execute(
                'SELECT dataset FROM lifecycle_raw_versions WHERE id=?', (raw_version_id,),
            ).fetchone()
            if not row or row[0] != dataset:
                raise ValueError('선택한 원본이 자료 종류와 일치하지 않습니다.')
            db.execute('''INSERT OR REPLACE INTO lifecycle_source_selections
                VALUES(?,?,?,?)''', (dataset, raw_version_id, evidence, _now()))

    def source_selection(self):
        with self.connect() as db:
            rows = db.execute('''SELECT dataset,raw_version_id FROM lifecycle_source_selections
                ORDER BY dataset''').fetchall()
        return {dataset: int(raw_id) for dataset, raw_id in rows}

    def selected_source_rows(self):
        frames = [self.source_rows(raw_id) for raw_id in self.source_selection().values()]
        frames = [frame for frame in frames if not frame.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _insert_frame(self, db, table, frame):
        if frame.empty:
            return
        columns = frame.columns.tolist()
        placeholders = ','.join('?' for _ in columns)
        rows = [tuple(_clean(value) for value in row) for row in frame.itertuples(index=False, name=None)]
        db.executemany(
            f'INSERT INTO {table} ({",".join(columns)}) VALUES({placeholders})', rows,
        )

    def promote_model(self, model, source_selection):
        fingerprint = lifecycle_fingerprint(model)
        with self.connect() as db:
            selected_ids = {int(value) for value in source_selection.values()}
            evidence_rows = {
                (int(raw_id), int(row_number))
                for raw_id, row_number in db.execute(
                    'SELECT raw_version_id,source_row_number FROM lifecycle_source_rows'
                ).fetchall()
            }
            for name in [
                'issuers', 'securities', 'identifiers', 'names',
                'events', 'lineage', 'intervals',
            ]:
                frame = getattr(model, name)
                if frame.empty:
                    continue
                if not {'raw_version_id', 'source_row_number'} <= set(frame.columns):
                    raise ValueError(f'{name} 정체성 자료에 원본 행 근거가 없습니다.')
                for raw_id, row_number in frame[
                    ['raw_version_id', 'source_row_number']
                ].itertuples(index=False, name=None):
                    evidence = (int(raw_id), int(row_number))
                    if int(raw_id) not in selected_ids or evidence not in evidence_rows:
                        raise ValueError(f'{name} 정체성 자료의 원본 행 근거를 확인할 수 없습니다.')
            previous = db.execute('SELECT revision FROM lifecycle_model_meta WHERE id=1').fetchone()
            revision = (int(previous[0]) if previous else 0) + 1
            for table in [
                'lifecycle_issuers', 'lifecycle_securities', 'lifecycle_identifiers',
                'lifecycle_names', 'lifecycle_events', 'lifecycle_lineage',
                'lifecycle_intervals', 'lifecycle_findings',
            ]:
                db.execute(f'DELETE FROM {table}')
            self._insert_frame(db, 'lifecycle_issuers', model.issuers)
            self._insert_frame(db, 'lifecycle_securities', model.securities)
            if self.promotion_hook:
                self.promotion_hook('after_identities')
            self._insert_frame(db, 'lifecycle_identifiers', model.identifiers)
            self._insert_frame(db, 'lifecycle_names', model.names)
            self._insert_frame(db, 'lifecycle_events', model.events)
            self._insert_frame(db, 'lifecycle_lineage', model.lineage)
            self._insert_frame(db, 'lifecycle_intervals', model.intervals)
            finding_rows = [(
                revision, item.severity, item.rule_id, item.message,
                item.security_id, item.effective_date, _now(),
            ) for item in model.findings]
            if finding_rows:
                db.executemany('''INSERT INTO lifecycle_findings
                    (model_revision,severity,rule_id,message,security_id,effective_date,created_at)
                    VALUES(?,?,?,?,?,?,?)''', finding_rows)
            db.execute('''INSERT OR REPLACE INTO lifecycle_model_meta
                VALUES(1,?,?,?,?)''', (
                revision, fingerprint,
                json.dumps(dict(source_selection), ensure_ascii=False, sort_keys=True), _now(),
            ))
        return revision

    def current_fingerprint(self):
        with self.connect() as db:
            row = db.execute('SELECT fingerprint FROM lifecycle_model_meta WHERE id=1').fetchone()
        return row[0] if row else None

    def listed_securities(self, as_of_date):
        day = pd.Timestamp(as_of_date).date().isoformat()
        with self.connect() as db:
            return pd.read_sql_query('''SELECT security_id,issuer_id,standard_code,short_code,
                name,market,security_kind,valid_from,valid_to,raw_version_id,source_row_number
                FROM lifecycle_intervals
                WHERE valid_from<=? AND (valid_to IS NULL OR valid_to>?)
                ORDER BY market,short_code''', db, params=(day, day))

    def lifecycle_timeline(self, security_id):
        with self.connect() as db:
            events = pd.read_sql_query('''SELECT * FROM lifecycle_events
                WHERE security_id=? ORDER BY effective_date,event_type''', db, params=(security_id,))
            intervals = pd.read_sql_query('''SELECT * FROM lifecycle_intervals
                WHERE security_id=? ORDER BY valid_from''', db, params=(security_id,))
        return {
            'events': events.where(pd.notna(events), None).to_dict(orient='records'),
            'intervals': intervals.where(pd.notna(intervals), None).to_dict(orient='records'),
        }

    def security_catalog(self):
        with self.connect() as db:
            return pd.read_sql_query('''SELECT i.security_id,i.short_code,i.name,i.market
                FROM lifecycle_intervals i
                JOIN (SELECT security_id,MAX(valid_from) AS valid_from
                      FROM lifecycle_intervals GROUP BY security_id) latest
                  ON latest.security_id=i.security_id AND latest.valid_from=i.valid_from
                ORDER BY i.market,i.short_code''', db)

    def readiness(self, start_date, end_date):
        reasons = []
        required = {'security_master', 'new_listings', 'delistings', 'identifier_changes'}
        selected = self.source_selection()
        missing = sorted(required - set(selected))
        if missing:
            reasons.append(f'선택되지 않은 공식 자료: {", ".join(missing)}')
        versions = self.raw_versions()
        if not versions.empty:
            latest = versions.groupby('dataset').id.max().to_dict()
            if any(selected.get(dataset) != int(raw_id) for dataset, raw_id in latest.items()):
                reasons.append('검토되지 않은 원본 버전이 있습니다.')
        with self.connect() as db:
            meta = db.execute('''SELECT revision,fingerprint,source_selection_json
                FROM lifecycle_model_meta WHERE id=1''').fetchone()
            blocking = db.execute('''SELECT COUNT(*) FROM lifecycle_findings
                WHERE severity IN ('blocking','quarantine')''').fetchone()[0]
            latest_rebuild = db.execute(
                'SELECT status FROM lifecycle_rebuilds ORDER BY id DESC LIMIT 1'
            ).fetchone()
            reconciliation = db.execute('''SELECT daily_coverage,unresolved_dates_json,
                corporate_action_pending FROM lifecycle_reconciliation_runs
                WHERE start_date=? AND end_date=? ORDER BY id DESC LIMIT 1''', (
                pd.Timestamp(start_date).date().isoformat(),
                pd.Timestamp(end_date).date().isoformat(),
            )).fetchone()
        if not meta:
            reasons.append('승격된 생애주기 모델이 없습니다.')
        elif json.loads(meta[2]) != selected:
            reasons.append('승격 모델과 선택 원본이 다릅니다.')
        if latest_rebuild and latest_rebuild[0] == 'failed':
            reasons.append('최근 재빌드 검증 실패가 해결되지 않았습니다.')
        if blocking:
            reasons.append(f'해결되지 않은 차단 항목 {blocking}건이 있습니다.')
        daily_coverage = reconciliation[0] if reconciliation else None
        unresolved_dates = json.loads(reconciliation[1]) if reconciliation else []
        corporate_action_pending = bool(reconciliation[2]) if reconciliation else False
        if unresolved_dates:
            reasons.append(f'일별 시장 대조가 필요한 날짜 {len(unresolved_dates)}일이 있습니다.')
        if corporate_action_pending:
            reasons.append('기업행사 검토 신호가 남아 있습니다.')
        return {
            'lifecycle_ready': not reasons,
            'blocking_reasons': reasons,
            'fingerprint': meta[1] if meta else None,
            'model_revision': int(meta[0]) if meta else None,
            'start_date': pd.Timestamp(start_date).date().isoformat(),
            'end_date': pd.Timestamp(end_date).date().isoformat(),
            'daily_coverage': daily_coverage,
            'unresolved_dates': unresolved_dates,
            'corporate_action_pending': corporate_action_pending,
            'investability_ready': False,
            'corporate_action_ready': False,
            'official_backtest_ready': False,
        }

    def save_reconciliation(self, start_date, end_date, findings, daily_coverage,
                            unresolved_dates, corporate_action_pending):
        with self.connect() as db:
            cursor = db.execute('''INSERT INTO lifecycle_reconciliation_runs
                (start_date,end_date,daily_coverage,unresolved_dates_json,
                 corporate_action_pending,created_at) VALUES(?,?,?,?,?,?)''', (
                start_date, end_date, float(daily_coverage),
                json.dumps(list(unresolved_dates), ensure_ascii=False),
                int(bool(corporate_action_pending)), _now(),
            ))
            run_id = int(cursor.lastrowid)
            rows = [(
                run_id, item.severity, item.rule_id, item.message,
                item.security_id, item.effective_date,
                item.lifecycle_raw_version_id, item.market_raw_version_id,
                item.lifecycle_value, item.market_value,
            ) for item in findings]
            if rows:
                db.executemany('''INSERT INTO lifecycle_reconciliation_findings
                    (run_id,severity,rule_id,message,security_id,effective_date,
                     lifecycle_raw_version_id,market_raw_version_id,lifecycle_value,market_value)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', rows)
        return run_id

    def reconciliation_findings(self, start_date, end_date):
        with self.connect() as db:
            return pd.read_sql_query('''SELECT f.* FROM lifecycle_reconciliation_findings f
                JOIN lifecycle_reconciliation_runs r ON r.id=f.run_id
                WHERE r.id=(SELECT id FROM lifecycle_reconciliation_runs
                    WHERE start_date=? AND end_date=? ORDER BY id DESC LIMIT 1)
                ORDER BY f.id''', db, params=(start_date, end_date))

    def record_rebuild(self, parser_version, rules_version, source_selection,
                       expected_fingerprint, actual_fingerprint, status, message):
        with self.connect() as db:
            cursor = db.execute('''INSERT INTO lifecycle_rebuilds
                (parser_version,rules_version,source_selection_json,expected_fingerprint,
                 actual_fingerprint,status,message,created_at) VALUES(?,?,?,?,?,?,?,?)''', (
                str(parser_version), str(rules_version),
                json.dumps(dict(source_selection), ensure_ascii=False, sort_keys=True),
                expected_fingerprint, actual_fingerprint, status, str(message), _now(),
            ))
            return int(cursor.lastrowid)

    def rebuilds(self):
        with self.connect() as db:
            return pd.read_sql_query('SELECT * FROM lifecycle_rebuilds ORDER BY id', db)

    def record_manual_review(self, source_url, retrieved_at, raw_sha256, example_type,
                             expected_event, derived_interval, reviewer_result, notes=''):
        with self.connect() as db:
            cursor = db.execute('''INSERT INTO lifecycle_manual_reviews
                (source_url,retrieved_at,raw_sha256,example_type,expected_event,
                 derived_interval,reviewer_result,notes,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)''', (
                str(source_url), str(retrieved_at), str(raw_sha256), str(example_type),
                str(expected_event), str(derived_interval), str(reviewer_result),
                str(notes), _now(),
            ))
            return int(cursor.lastrowid)

    def manual_reviews(self):
        with self.connect() as db:
            return pd.read_sql_query(
                'SELECT * FROM lifecycle_manual_reviews ORDER BY id', db,
            )
