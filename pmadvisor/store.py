"""SQLite store: analysis runs, recommendations with their human decisions, LLM calls, and a hash-chained audit
log (each entry contains the SHA-256 of the previous one, so a changed or deleted entry breaks the chain)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, created TEXT, source TEXT, label TEXT, scenario TEXT,
  log_path TEXT, kpis TEXT);
CREATE TABLE IF NOT EXISTS recommendations (id INTEGER PRIMARY KEY, run_id INTEGER, lever TEXT, title TEXT,
  priority INTEGER, rationale TEXT, estimate TEXT, status TEXT, source TEXT, created_by TEXT, created TEXT,
  decided_by TEXT, decided_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, run_id INTEGER, created TEXT, source TEXT, model TEXT,
  text TEXT, grounding TEXT, tool_calls TEXT);
CREATE TABLE IF NOT EXISTS llm_calls (id INTEGER PRIMARY KEY, ts TEXT, model TEXT, input_tokens INTEGER,
  output_tokens INTEGER, cost_usd REAL, latency_ms INTEGER);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, ts TEXT, actor TEXT, action TEXT, payload TEXT,
  prev_hash TEXT, hash TEXT);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- audit
    def audit(self, actor: str, action: str, payload: dict) -> None:
        with self.lock:
            row = self.db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else "0" * 64
            ts = _now()
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
            h = hashlib.sha256(f"{prev}|{ts}|{actor}|{action}|{body}".encode()).hexdigest()
            self.db.execute("INSERT INTO audit (ts, actor, action, payload, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                            (ts, actor, action, body, prev, h))
            self.db.commit()

    def verify_audit(self) -> dict:
        prev = "0" * 64
        rows = self.db.execute("SELECT * FROM audit ORDER BY id").fetchall()
        for r in rows:
            h = hashlib.sha256(f"{prev}|{r['ts']}|{r['actor']}|{r['action']}|{r['payload']}".encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return {"ok": False, "entries": len(rows), "broken_at": r["id"]}
            prev = h
        return {"ok": True, "entries": len(rows)}

    def audit_log(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    # ---------------------------------------------------------------- runs
    def add_run(self, source: str, label: str, scenario: dict | None, log_path: str | None, kpis: dict) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO runs (created, source, label, scenario, log_path, kpis) VALUES (?,?,?,?,?,?)",
                                  (_now(), source, label, json.dumps(scenario, ensure_ascii=False) if scenario else None,
                                   log_path, json.dumps(kpis, ensure_ascii=False)))
            self.db.commit()
            return int(cur.lastrowid)

    def runs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT id, created, source, label FROM runs ORDER BY id DESC")]

    # ---------------------------------------------------------------- recommendations
    def add_recommendation(self, run_id: int, lever: str, title: str, priority: int, rationale: str, estimate: dict,
                           source: str, created_by: str) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO recommendations (run_id, lever, title, priority, rationale, estimate, status, source, "
                "created_by, created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, lever, title, priority, rationale, json.dumps(estimate, ensure_ascii=False), "PENDING",
                 source, created_by, _now()))
            self.db.commit()
            return int(cur.lastrowid)

    def recommendation(self, rec_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM recommendations WHERE id=?", (rec_id,)).fetchone()
        return _rec(r) if r else None

    def recommendations(self, run_id: int) -> list[dict]:
        rows = self.db.execute("SELECT * FROM recommendations WHERE run_id=? ORDER BY priority, id", (run_id,)).fetchall()
        return [_rec(r) for r in rows]

    def decide(self, rec_id: int, status: str, user: str, note: str) -> None:
        with self.lock:
            self.db.execute("UPDATE recommendations SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
                            (status, user, _now(), note, rec_id))
            self.db.commit()

    # ---------------------------------------------------------------- reports / llm
    def add_report(self, run_id: int, source: str, model: str | None, text: str, grounding: dict, tool_calls: list) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO reports (run_id, created, source, model, text, grounding, tool_calls) "
                                  "VALUES (?,?,?,?,?,?,?)", (run_id, _now(), source, model, text,
                                                             json.dumps(grounding, ensure_ascii=False),
                                                             json.dumps(tool_calls, ensure_ascii=False, default=str)))
            self.db.commit()
            return int(cur.lastrowid)

    def latest_report(self, run_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM reports WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["grounding"] = json.loads(d["grounding"])
        d["tool_calls"] = json.loads(d["tool_calls"])
        return d

    def log_llm(self, model: str, tin: int, tout: int, cost: float, latency_ms: int) -> None:
        with self.lock:
            self.db.execute("INSERT INTO llm_calls (ts, model, input_tokens, output_tokens, cost_usd, latency_ms) "
                            "VALUES (?,?,?,?,?,?)", (_now(), model, tin, tout, cost, latency_ms))
            self.db.commit()


def _rec(r) -> dict:
    d = dict(r)
    d["estimate"] = json.loads(d["estimate"])
    return d
