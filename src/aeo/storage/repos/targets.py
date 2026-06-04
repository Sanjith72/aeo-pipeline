"""Reads/writes for clients and competitors."""

from __future__ import annotations

from typing import Literal

from ..db import transaction
from ..models import Target

Kind = Literal["client", "competitor"]


def by_name(name: str, kind: Kind) -> Target | None:
    table = "clients" if kind == "client" else "competitors"
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id, name, domain FROM {table} WHERE name = %s", (name,))
        row = cur.fetchone()
    if not row:
        return None
    return Target(id=row["id"], name=row["name"], domain=row["domain"], kind=kind)


def find(name: str) -> Target | None:
    """Try client then competitor."""
    return by_name(name, "client") or by_name(name, "competitor")


def list_all(kind: Kind, active_only: bool = True) -> list[Target]:
    table = "clients" if kind == "client" else "competitors"
    sql = f"SELECT id, name, domain FROM {table}"
    if active_only:
        sql += " WHERE is_active = TRUE"
    sql += " ORDER BY name"
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [Target(id=r["id"], name=r["name"], domain=r["domain"], kind=kind)
                for r in cur.fetchall()]
