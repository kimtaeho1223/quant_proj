import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, created TEXT NOT NULL, note TEXT NOT NULL, payload TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def load_account(self):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM account WHERE id=1').fetchone()
        if not row:
            return {}, 10_000_000
        data = json.loads(row[0])
        return data['holdings'], data['cash']

    def save_account(self, holdings, cash):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO account VALUES (1, ?)', (json.dumps(dict(holdings=holdings, cash=cash)),))

    def save_snapshot(self, result, note, settings):
        payload = json.dumps(dict(result=result, settings=settings), ensure_ascii=False)
        with self.connect() as db:
            db.execute('INSERT INTO snapshots (created, note, payload) VALUES (?, ?, ?)',
                       (datetime.now(timezone.utc).isoformat(), note, payload))

    def history(self):
        with self.connect() as db:
            rows = db.execute('SELECT id, created, note, payload FROM snapshots ORDER BY id DESC').fetchall()
        return [dict(id=r[0], created=r[1], note=r[2], **json.loads(r[3])) for r in rows]
