"""SQLite access: studies, masks, jobs, cached measurements. One short-lived connection per call (WAL)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS studies (
  id TEXT PRIMARY KEY,
  alias TEXT,
  patient_alias TEXT,                 -- grouping for future longitudinal comparison
  timepoint_label TEXT,
  scan_date TEXT,                     -- user-entered, optional (NIfTI does not carry it)
  sequence_label TEXT NOT NULL,
  upload_label TEXT,
  state TEXT NOT NULL,                -- preparing | ready | failed
  failure_code TEXT, failure_message TEXT,
  original_file TEXT,
  file_bytes INTEGER,
  geometry TEXT,
  display_revision INTEGER NOT NULL DEFAULT 1,
  default_window TEXT,
  intensity_range TEXT,
  selected_mask_id TEXT,
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS masks (
  id TEXT PRIMARY KEY,
  study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
  state TEXT NOT NULL,                -- preparing | ready | failed
  source TEXT NOT NULL,               -- uploaded | model | synthetic_demo
  revision INTEGER NOT NULL DEFAULT 1,
  failure_code TEXT, failure_message TEXT,
  statistics TEXT,
  candidates TEXT,
  provenance TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
  model_id TEXT NOT NULL,
  state TEXT NOT NULL,                -- queued | running | succeeded | failed
  stage TEXT,
  error_code TEXT, error_message TEXT,
  output_mask_id TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_study
  ON jobs(study_id) WHERE state IN ('queued','running');
CREATE TABLE IF NOT EXISTS measurements (
  mask_id TEXT NOT NULL REFERENCES masks(id) ON DELETE CASCADE,
  display_revision INTEGER NOT NULL,
  plane TEXT NOT NULL, idx INTEGER NOT NULL,
  payload TEXT NOT NULL,
  PRIMARY KEY (mask_id, display_revision, plane, idx)
);
CREATE INDEX IF NOT EXISTS studies_created ON studies(created_at DESC, id);
"""

JSON_COLS = {"geometry", "default_window", "intensity_range", "statistics", "candidates", "provenance"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_COLS & d.keys():
        if d[k] is not None:
            d[k] = json.loads(d[k])
    return d


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.conn() as c:
            return _decode(c.execute(sql, args).fetchone())

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.conn() as c:
            return [_decode(r) for r in c.execute(sql, args).fetchall()]

    def update(self, table: str, id_: str, **fields: Any) -> None:
        if table in ("studies", "masks"):
            fields["updated_at"] = utcnow()
        for k in JSON_COLS & fields.keys():
            if fields[k] is not None:
                fields[k] = json.dumps(fields[k])
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), id_))

    # -- studies / masks -------------------------------------------------
    def insert_study(self, id_: str, alias, seq: str, upload_label, *, patient_alias=None, timepoint_label=None,
                     scan_date=None, is_demo: bool = False) -> None:
        now = utcnow()
        with self.conn() as c:
            c.execute(
                "INSERT INTO studies(id,alias,patient_alias,timepoint_label,scan_date,sequence_label,upload_label,"
                "state,is_demo,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (id_, alias, patient_alias, timepoint_label, scan_date, seq, upload_label, "preparing", int(is_demo), now, now),
            )

    def insert_mask(self, id_: str, study_id: str, source: str) -> None:
        now = utcnow()
        with self.conn() as c:
            c.execute("INSERT INTO masks(id,study_id,state,source,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                      (id_, study_id, "preparing", source, now, now))

    def get_study(self, id_: str) -> dict | None:
        return self.one("SELECT * FROM studies WHERE id=?", (id_,))

    def get_mask(self, study_id: str, mask_id: str) -> dict | None:
        return self.one("SELECT * FROM masks WHERE id=? AND study_id=?", (mask_id, study_id))

    def masks_of(self, study_id: str) -> list[dict]:
        return self.all("SELECT * FROM masks WHERE study_id=? ORDER BY created_at, id", (study_id,))

    def get_job(self, id_: str) -> dict | None:
        return self.one("SELECT * FROM jobs WHERE id=?", (id_,))

    def has_active_work(self, study_id: str) -> bool:
        with self.conn() as c:
            n = c.execute("SELECT COUNT(*) FROM jobs WHERE study_id=? AND state IN ('queued','running')", (study_id,)).fetchone()[0]
            m = c.execute("SELECT COUNT(*) FROM masks WHERE study_id=? AND state='preparing'", (study_id,)).fetchone()[0]
            s = c.execute("SELECT COUNT(*) FROM studies WHERE id=? AND state='preparing'", (study_id,)).fetchone()[0]
        return bool(n or m or s)

    # -- measurement cache ----------------------------------------------
    def get_measurement(self, mask_id: str, rev: int, plane: str, idx: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT payload FROM measurements WHERE mask_id=? AND display_revision=? AND plane=? AND idx=?",
                          (mask_id, rev, plane, idx)).fetchone()
        return json.loads(r[0]) if r else None

    def put_measurements(self, mask_id: str, rev: int, items: list[tuple[str, int, dict]]) -> None:
        with self.tx() as c:
            c.executemany(
                "INSERT OR REPLACE INTO measurements(mask_id,display_revision,plane,idx,payload) VALUES (?,?,?,?,?)",
                [(mask_id, rev, p, i, json.dumps(d)) for p, i, d in items],
            )

    def measurements_of(self, mask_id: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT payload FROM measurements WHERE mask_id=? ORDER BY plane, idx", (mask_id,)).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # -- jobs ------------------------------------------------------------
    def insert_job(self, id_: str, study_id: str, model_id: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO jobs(id,study_id,model_id,state,stage,created_at) VALUES (?,?,?,?,?,?)",
                      (id_, study_id, model_id, "queued", "queued", utcnow()))

    def claim_job(self) -> dict | None:
        with self.tx() as c:
            if c.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                return None
            r = c.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at, id LIMIT 1").fetchone()
            if not r:
                return None
            c.execute("UPDATE jobs SET state='running', stage='starting', started_at=? WHERE id=?", (utcnow(), r["id"]))
            return _decode(c.execute("SELECT * FROM jobs WHERE id=?", (r["id"],)).fetchone())

    def fail_interrupted_jobs(self) -> int:
        with self.conn() as c:
            cur = c.execute(
                "UPDATE jobs SET state='failed', error_code='WORKER_RESTARTED',"
                " error_message='The worker restarted while this job was running.', finished_at=? WHERE state='running'",
                (utcnow(),))
            return cur.rowcount

    def fail_interrupted_preparation(self) -> None:
        msg = "The server restarted during preparation. Delete this case and upload again."
        with self.conn() as c:
            c.execute("UPDATE studies SET state='failed', failure_code='PREPARATION_INTERRUPTED', failure_message=? WHERE state='preparing'", (msg,))
            c.execute("UPDATE masks SET state='failed', failure_code='PREPARATION_INTERRUPTED', failure_message=? WHERE state='preparing'", (msg,))
