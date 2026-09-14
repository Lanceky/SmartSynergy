"""SQLite persistence: watches, runs, sources, claims, dossiers, review items, decisions.

Design rules that matter for correctness:
  * one run at a time per watch  -> partial unique index on runs(watch_id) WHERE status='running'
  * alerts are idempotent        -> unique (watch_id, hypothesis_id, fingerprint_after)
  * dossier + review item + run status commit in ONE transaction
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from smartsynergy.schemas import (
    Dossier,
    EvidenceClaim,
    EvidenceProfile,
    ReviewItem,
    Run,
    SourceRecord,
    Watch,
    utcnow,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS watches (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_checked_at TEXT,
    watermark TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    watch_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active
    ON runs(watch_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS runs_by_watch ON runs(watch_id, started_at DESC);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    watch_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    first_seen_run TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS claims_by_run ON claims(run_id);

CREATE TABLE IF NOT EXISTS dossiers (
    id TEXT PRIMARY KEY,
    watch_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (hypothesis_id, version)
);

CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY,
    watch_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    fingerprint_after TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (watch_id, hypothesis_id, fingerprint_after)
);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class RunInProgress(RuntimeError):
    """Another run holds the lock for this watch."""


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: Streamlit reruns the script on a different thread each
        # time. sqlite3.threadsafety == 3 (serialized), and multi-statement transactions are
        # additionally guarded by self._lock below.
        self.conn = sqlite3.connect(
            self.path, isolation_level=None, timeout=30, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ watches
    def save_watch(self, watch: Watch) -> None:
        self.conn.execute(
            """INSERT INTO watches (id, payload, enabled, created_at) VALUES (?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, enabled=excluded.enabled""",
            (watch.id, watch.model_dump_json(), int(watch.enabled), _iso(watch.created_at)),
        )

    def get_watch(self, watch_id: str) -> Watch | None:
        row = self.conn.execute("SELECT payload FROM watches WHERE id=?", (watch_id,)).fetchone()
        return Watch.model_validate_json(row["payload"]) if row else None

    def list_watches(self) -> list[Watch]:
        rows = self.conn.execute("SELECT payload FROM watches ORDER BY created_at").fetchall()
        return [Watch.model_validate_json(r["payload"]) for r in rows]

    def watch_meta(self, watch_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT last_checked_at, watermark, next_run_at FROM watches WHERE id=?", (watch_id,)
        ).fetchone()
        return dict(row) if row else {}

    def set_watermark(self, watch_id: str, watermark: str) -> None:
        """Only advance after a fully complete search; truncated/failed runs keep the old value."""
        self.conn.execute("UPDATE watches SET watermark=? WHERE id=?", (watermark, watch_id))

    def touch_checked(self, watch_id: str, when: datetime | None = None) -> None:
        self.conn.execute(
            "UPDATE watches SET last_checked_at=? WHERE id=?", (_iso(when or utcnow()), watch_id)
        )

    # ------------------------------------------------------------------ runs
    def start_run(self, run: Run) -> Run:
      with self._lock:
        self._sweep_stale_runs()
        try:
            self.conn.execute(
                "INSERT INTO runs (id, watch_id, status, payload, started_at) VALUES (?,?,?,?,?)",
                (run.id, run.watch_id, run.status, run.model_dump_json(), _iso(run.started_at)),
            )
        except sqlite3.IntegrityError as exc:
            raise RunInProgress(f"a run is already active for watch {run.watch_id}") from exc
        return run

    def _sweep_stale_runs(self, max_age_s: int = 1800) -> None:
        cutoff = (utcnow() - timedelta(seconds=max_age_s)).isoformat()
        self.conn.execute(
            "UPDATE runs SET status='failed_stale', finished_at=? WHERE status='running' AND started_at < ?",
            (_iso(utcnow()), cutoff),
        )

    def finish_run(self, run: Run) -> None:
        run.finished_at = run.finished_at or utcnow()
        self.conn.execute(
            "UPDATE runs SET status=?, payload=?, finished_at=? WHERE id=?",
            (run.status, run.model_dump_json(), _iso(run.finished_at), run.id),
        )

    def list_runs(self, watch_id: str | None = None, limit: int = 25) -> list[Run]:
        sql = "SELECT payload FROM runs"
        args: tuple = ()
        if watch_id:
            sql += " WHERE watch_id=?"
            args = (watch_id,)
        sql += " ORDER BY started_at DESC LIMIT ?"
        rows = self.conn.execute(sql, (*args, limit)).fetchall()
        return [Run.model_validate_json(r["payload"]) for r in rows]

    def runs_today(self, watch_id: str) -> int:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM runs WHERE watch_id=? AND started_at >= ?",
            (watch_id, _iso(start)),
        ).fetchone()
        return int(row["c"])

    # ------------------------------------------------------------------ sources
    def known_source_ids(self, watch_id: str) -> set[str]:
        rows = self.conn.execute("SELECT source_id FROM sources WHERE watch_id=?", (watch_id,)).fetchall()
        return {r["source_id"] for r in rows}

    def upsert_sources(self, watch_id: str, run_id: str, records: Iterable[SourceRecord]) -> list[str]:
        """Returns the source_ids that are new or whose stored content changed."""
        changed: list[str] = []
        for rec in records:
            row = self.conn.execute(
                "SELECT content_hash FROM sources WHERE source_id=?", (rec.source_id,)
            ).fetchone()
            if row is None:
                changed.append(rec.source_id)
                self.conn.execute(
                    """INSERT INTO sources (source_id, watch_id, payload, content_hash, first_seen_run, updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    (rec.source_id, watch_id, rec.model_dump_json(), rec.content_hash, run_id, _iso(utcnow())),
                )
            elif row["content_hash"] != rec.content_hash:
                changed.append(rec.source_id)
                self.conn.execute(
                    "UPDATE sources SET payload=?, content_hash=?, updated_at=? WHERE source_id=?",
                    (rec.model_dump_json(), rec.content_hash, _iso(utcnow()), rec.source_id),
                )
        return changed

    def get_source(self, source_id: str) -> SourceRecord | None:
        row = self.conn.execute("SELECT payload FROM sources WHERE source_id=?", (source_id,)).fetchone()
        return SourceRecord.model_validate_json(row["payload"]) if row else None

    def get_sources(self, source_ids: Iterable[str]) -> dict[str, SourceRecord]:
        out: dict[str, SourceRecord] = {}
        for sid in source_ids:
            rec = self.get_source(sid)
            if rec:
                out[sid] = rec
        return out

    # ------------------------------------------------------------------ claims
    def save_claims(self, claims: Iterable[EvidenceClaim]) -> None:
        for c in claims:
            self.conn.execute(
                """INSERT OR REPLACE INTO claims (claim_id, hypothesis_id, run_id, source_id, payload, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (c.claim_id, c.hypothesis_id, c.run_id, c.source_id, c.model_dump_json(), _iso(c.created_at)),
            )

    def get_claims(self, claim_ids: Iterable[str]) -> list[EvidenceClaim]:
        ids = list(claim_ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT payload FROM claims WHERE claim_id IN ({marks})", ids
        ).fetchall()
        return [EvidenceClaim.model_validate_json(r["payload"]) for r in rows]

    # ------------------------------------------------------------------ dossiers
    def latest_dossier(self, hypothesis_id: str) -> Dossier | None:
        row = self.conn.execute(
            "SELECT payload FROM dossiers WHERE hypothesis_id=? ORDER BY version DESC LIMIT 1",
            (hypothesis_id,),
        ).fetchone()
        return Dossier.model_validate_json(row["payload"]) if row else None

    def dossier_versions(self, hypothesis_id: str) -> list[Dossier]:
        rows = self.conn.execute(
            "SELECT payload FROM dossiers WHERE hypothesis_id=? ORDER BY version DESC", (hypothesis_id,)
        ).fetchall()
        return [Dossier.model_validate_json(r["payload"]) for r in rows]

    # ------------------------------------------------------------------ atomic commit
    def commit_result(
        self,
        run: Run,
        dossier: Dossier | None,
        claims: list[EvidenceClaim],
        review: ReviewItem | None,
    ) -> bool:
        """Persist a hypothesis result atomically. Returns True if a NEW review item was created."""
        created = False
        self._lock.acquire()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            if claims:
                self.save_claims(claims)
            if dossier:
                self.conn.execute(
                    """INSERT OR REPLACE INTO dossiers
                       (id, watch_id, hypothesis_id, version, fingerprint, payload, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        dossier.id,
                        dossier.watch_id,
                        dossier.hypothesis_id,
                        dossier.version,
                        dossier.fingerprint,
                        dossier.model_dump_json(),
                        _iso(dossier.created_at),
                    ),
                )
            if review:
                cur = self.conn.execute(
                    """INSERT OR IGNORE INTO review_items
                       (id, watch_id, hypothesis_id, fingerprint_after, state, payload, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        review.id,
                        review.watch_id,
                        review.hypothesis_id,
                        review.fingerprint_after,
                        review.state,
                        review.model_dump_json(),
                        _iso(review.created_at),
                    ),
                )
                created = cur.rowcount > 0
            self.conn.execute(
                "UPDATE runs SET status=?, payload=? WHERE id=?",
                (run.status, run.model_dump_json(), run.id),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        finally:
            self._lock.release()
        return created

    # ------------------------------------------------------------------ review inbox
    def list_inbox(self, watch_id: str | None = None, state: str | None = "open") -> list[ReviewItem]:
        sql, args = "SELECT payload FROM review_items WHERE 1=1", []
        if watch_id:
            sql += " AND watch_id=?"
            args.append(watch_id)
        if state:
            sql += " AND state=?"
            args.append(state)
        sql += " ORDER BY created_at DESC"
        items = [ReviewItem.model_validate_json(r["payload"]) for r in self.conn.execute(sql, args)]
        return sorted(items, key=lambda i: (-i.urgency, i.created_at))

    def record_decision(self, item_id: str, state: str, note: str = "") -> None:
        row = self.conn.execute("SELECT payload FROM review_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            return
        item = ReviewItem.model_validate_json(row["payload"])
        item.state = state  # type: ignore[assignment]
        item.decision_note = note
        item.decided_at = utcnow()
        self.conn.execute(
            "UPDATE review_items SET state=?, payload=? WHERE id=?", (state, item.model_dump_json(), item_id)
        )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
