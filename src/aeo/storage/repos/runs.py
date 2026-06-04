"""Crawl-run lifecycle (start, finish, fail)."""

from __future__ import annotations

import uuid
from datetime import datetime

from ..db import transaction
from ..models import CrawlRun


def new_run_key() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"run_{ts}_{uuid.uuid4().hex[:6]}"


def start(label: str | None = None, run_key: str | None = None) -> CrawlRun:
    key = run_key or new_run_key()
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO crawl_runs (run_key, label) VALUES (%s, %s) "
            "RETURNING id, run_key, label, started_at, status",
            (key, label),
        )
        row = cur.fetchone()
    return CrawlRun(
        id=row["id"], run_key=row["run_key"], label=row["label"],
        started_at=row["started_at"], status=row["status"],
    )


def finish(run_id: int, status: str = "succeeded", notes: str | None = None) -> None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE crawl_runs SET finished_at = NOW(), status = %s, notes = %s WHERE id = %s",
            (status, notes, run_id),
        )


def get(run_id: int) -> CrawlRun | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, run_key, label, started_at, status FROM crawl_runs WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return CrawlRun(**{k: row[k] for k in ("id", "run_key", "label", "started_at", "status")})
