import contextlib
import json
import sqlite3
import time
import uuid


class Store:
    def __init__(self, directory):
        self.path = directory / "history.sqlite3"
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY, project TEXT NOT NULL, account TEXT NOT NULL,
              source TEXT NOT NULL, name TEXT NOT NULL, root TEXT NOT NULL,
              status TEXT NOT NULL, phase TEXT NOT NULL, created REAL NOT NULL,
              error TEXT NOT NULL DEFAULT '', workers INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS folders(
              job TEXT NOT NULL, id TEXT NOT NULL, local TEXT NOT NULL, folded TEXT NOT NULL,
              page TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(job,id), UNIQUE(job,folded));
            CREATE TABLE IF NOT EXISTS files(
              job TEXT NOT NULL, id TEXT NOT NULL, local TEXT NOT NULL, folded TEXT NOT NULL,
              metadata TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
              bytes INTEGER NOT NULL DEFAULT 0, part_sha TEXT NOT NULL DEFAULT '',
              sha TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(job,id), UNIQUE(job,folded));
            CREATE INDEX IF NOT EXISTS files_work ON files(job,status);
            CREATE TABLE IF NOT EXISTS usage(
              project TEXT NOT NULL, minute INTEGER NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
              units INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,
              limited INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project,minute));
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY, job TEXT NOT NULL, time REAL NOT NULL, code TEXT NOT NULL);
            """)
            db.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('running','pausing','stopping')")
            db.execute("UPDATE files SET status='pending' WHERE status='downloading'")

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db() as db:
            db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def job(self, identity):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
        return dict(row) if row else None

    def create_job(self, project, account, source, name, root, workers):
        identity = uuid.uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO jobs(id,project,account,source,name,root,status,phase,created,workers) VALUES(?,?,?,?,?,?,'paused','scan',?,?)",
                       (identity, project, account, source, name, root, time.time(), workers))
        return identity

    def change_job(self, identity, **fields):
        assert set(fields) <= {"status", "phase", "error"}
        with self.db() as db:
            db.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", (*fields.values(), identity))

    def event(self, job, code):
        with self.db() as db:
            db.execute("INSERT INTO events(job,time,code) VALUES(?,?,?)", (job, time.time(), code))
            db.execute("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0)-1000 FROM events)")

    def overview(self):
        with self.db() as db:
            jobs = [dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 30")]
            for job in jobs:
                stats = db.execute("""SELECT COUNT(*) AS total,
                  COALESCE(SUM(status='complete'),0) AS complete,
                  COALESCE(SUM(status='failed'),0) AS failed,
                  COALESCE(SUM(status='unsupported'),0) AS unsupported,
                  COALESCE(SUM(bytes),0) AS bytes FROM files WHERE job=?""", (job["id"],)).fetchone()
                job.update(dict(stats))
                job["folders"] = db.execute("SELECT COUNT(*) FROM folders WHERE job=? AND done=1", (job["id"],)).fetchone()[0]
        return jobs
