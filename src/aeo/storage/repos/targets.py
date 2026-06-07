"""Reads/writes for clients and competitors."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from ..db import transaction
from ..models import Target

Kind = Literal["client", "competitor"]


def target_host(domain: str) -> str:
    """Bare host key for a target: drop scheme, ``www.``, path, slashes."""
    d = (domain or "").strip().lower()
    d = urlsplit(d).netloc if "://" in d else d.split("/", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    return d.strip("/")


def upsert(name: str, domain: str, kind: Kind = "client", *, website_url: str | None = None) -> Target:
    """Register (or re-activate) a client/competitor so the audit path can target it.

    Idempotent on ``name``: re-running updates the domain/url and flips ``is_active``
    back on. ``website_url`` defaults to ``https://<host>``. The bare host is derived
    from ``domain`` so ``https://www.Acme.com/`` and ``acme.com`` register the same."""
    host = target_host(domain)
    url = website_url or f"https://{host}"
    table = "clients" if kind == "client" else "competitors"
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {table} (name, domain, website_url) VALUES (%s, %s, %s)
            ON CONFLICT (name) DO UPDATE
                SET domain = EXCLUDED.domain, website_url = EXCLUDED.website_url, is_active = TRUE
            RETURNING id, name, domain
            """,
            (name, host, url),
        )
        row = cur.fetchone()
    return Target(id=row["id"], name=row["name"], domain=row["domain"], kind=kind)


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
