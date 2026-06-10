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

    Idempotent on BOTH unique identities. The bare host is the real business key:
    re-auditing the same domain under a different label must reuse its row (the table
    has a unique ``domain`` constraint — a plain name-keyed upsert raised
    ``clients_domain_key`` violations here). The label is refreshed on reuse unless
    another row already owns the new name. For a new domain, an existing ``name``
    re-points that label (the original behavior). ``website_url`` defaults to
    ``https://<host>``; ``https://www.Acme.com/`` and ``acme.com`` register the same."""
    host = target_host(domain)
    url = website_url or f"https://{host}"
    table = "clients" if kind == "client" else "competitors"
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {table} t
               SET name = CASE WHEN EXISTS (SELECT 1 FROM {table} o
                                            WHERE o.name = %(name)s AND o.id <> t.id)
                               THEN t.name ELSE %(name)s END,
                   website_url = %(url)s,
                   is_active = TRUE
             WHERE t.domain = %(host)s
            RETURNING id, name, domain
            """,
            {"name": name, "url": url, "host": host},
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                f"""
                INSERT INTO {table} (name, domain, website_url) VALUES (%(name)s, %(host)s, %(url)s)
                ON CONFLICT (name) DO UPDATE
                    SET domain = EXCLUDED.domain, website_url = EXCLUDED.website_url, is_active = TRUE
                RETURNING id, name, domain
                """,
                {"name": name, "host": host, "url": url},
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
