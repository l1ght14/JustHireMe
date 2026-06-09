from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from data.sqlite.connection import DEFAULT_DB_PATH, get_connection, run_migrations


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobRecord:
    job_id: str
    kind: str
    status: str
    progress: int = 0
    input_json: dict[str, Any] = field(default_factory=dict)
    result_json: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""


class JobStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._initialized = False

    def init(self) -> None:
        if self._initialized:
            return
        run_migrations(self.db_path)
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_jobs(
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER DEFAULT 0,
                    input_json TEXT DEFAULT '{}',
                    result_json TEXT DEFAULT '{}',
                    error TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    started_at TEXT DEFAULT '',
                    finished_at TEXT DEFAULT ''
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
        self._initialized = True

    def create(self, kind: str, payload: dict[str, Any] | None = None) -> JobRecord:
        self.init()
        record = JobRecord(job_id=f"{kind}-{uuid.uuid4().hex[:12]}", kind=kind, status="queued", input_json=payload or {}, created_at=_now())
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO gateway_jobs(job_id, kind, status, progress, input_json, result_json, error, created_at, started_at, finished_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.job_id,
                    record.kind,
                    record.status,
                    record.progress,
                    json.dumps(record.input_json),
                    "{}",
                    "",
                    record.created_at,
                    "",
                    "",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return record

    def update(self, job_id: str, *, status: str | None = None, progress: int | None = None, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.init()
        # Atomic single-statement update of only the provided fields (COALESCE
        # keeps the others). The previous read-modify-write across two
        # connections was a lost-update race: two concurrent updates (e.g. the
        # ghost cycle's progress ticks vs. a cancel request) could clobber each
        # other, silently dropping a cancel. started_at/finished_at are stamped
        # in-SQL on the first transition into running / a terminal state.
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                UPDATE gateway_jobs SET
                    status = COALESCE(:status, status),
                    progress = COALESCE(:progress, progress),
                    result_json = COALESCE(:result_json, result_json),
                    error = COALESCE(:error, error),
                    started_at = CASE
                        WHEN started_at != '' THEN started_at
                        WHEN COALESCE(:status, status) = 'running' THEN :now
                        ELSE started_at END,
                    finished_at = CASE
                        WHEN finished_at != '' THEN finished_at
                        WHEN COALESCE(:status, status) IN ('succeeded', 'failed', 'cancelled') THEN :now
                        ELSE finished_at END
                WHERE job_id = :job_id
                """,
                {
                    "status": status,
                    "progress": progress,
                    "result_json": json.dumps(result) if result is not None else None,
                    "error": error,
                    "now": _now(),
                    "job_id": job_id,
                },
            )
            conn.commit()
        finally:
            conn.close()

    def request_cancel(self, job_id: str) -> None:
        self.update(job_id, status="cancel_requested")

    def is_cancel_requested(self, job_id: str) -> bool:
        record = self.get(job_id)
        return bool(record and record.status == "cancel_requested")

    def get(self, job_id: str) -> JobRecord | None:
        self.init()
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT job_id, kind, status, progress, input_json, result_json, error, created_at, started_at, finished_at
                FROM gateway_jobs WHERE job_id=?
                """,
                (job_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return JobRecord(
            job_id=row[0],
            kind=row[1],
            status=row[2],
            progress=int(row[3] or 0),
            input_json=json.loads(row[4] or "{}"),
            result_json=json.loads(row[5] or "{}"),
            error=row[6] or "",
            created_at=row[7] or "",
            started_at=row[8] or "",
            finished_at=row[9] or "",
        )


_job_store = JobStore()


def get_job_store() -> JobStore:
    return _job_store
