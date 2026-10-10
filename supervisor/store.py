import json
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cycles(id TEXT PRIMARY KEY, started REAL NOT NULL,
                status TEXT NOT NULL, report TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, day TEXT NOT NULL,
                reserved INTEGER NOT NULL, tokens INTEGER, status TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    def add(self, proposal):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO proposals(id,status,payload) VALUES(?,?,?)",
                            (proposal["id"], "queued", json.dumps(proposal)))

    def proposals(self):
        return [dict(id=row[0], status=row[1], attempts=row[2], payload=json.loads(row[3]))
                for row in self.db.execute("SELECT id,status,attempts,payload FROM proposals ORDER BY id")]

    def proposal_state(self, id, status, attempt=False):
        with self.db:
            self.db.execute("UPDATE proposals SET status=?,attempts=attempts+? WHERE id=?", (status, int(attempt), id))

    def cycle(self, id, report, status="running"):
        with self.db:
            self.db.execute("INSERT INTO cycles VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                            "status=excluded.status,report=excluded.report",
                            (id, time.time(), status, json.dumps(report)))

    def history(self, limit=30):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT report FROM cycles ORDER BY started DESC LIMIT ?", (limit,))]

    def reserve(self, id, day, config):
        # Reserve before launching, including failures and unknown/crashed usage.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            calls, tokens = self.db.execute(
                "SELECT count(*),coalesce(sum(coalesce(tokens,reserved)),0) FROM calls WHERE day=?", (day,)).fetchone()
            reservation = config["reserve_tokens_per_call"]
            if calls >= config["max_ai_calls_per_day"] or tokens + reservation > config["max_tokens_per_day"]:
                raise RuntimeError("daily_budget_exhausted")
            self.db.execute("INSERT INTO calls VALUES(?,?,?,?,?)", (id, day, reservation, None, "reserved"))

    def usage(self, id, tokens):
        with self.db:
            self.db.execute("UPDATE calls SET tokens=?,status=? WHERE id=?", (tokens, "completed", id))
