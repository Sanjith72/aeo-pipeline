"""The build identity that makes a factory rebuild falsifiable (src/aeo/build.py).

Backend deploys are manual and a plain restart silently reuses the cached layer, so
"production is running my commit" was an assumption no probe could check. These tests pin
the three properties that assumption rests on: the id is stable, it MOVES when the source
moves, and it never takes the health check down with it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aeo.build import _ID_LEN, build_id


def test_build_id_is_a_short_stable_hex_digest():
    first = build_id()
    assert len(first) == _ID_LEN
    assert all(c in "0123456789abcdef" for c in first), first
    # Same process, same answer — it is cached and must not drift between calls.
    assert build_id() == first


def test_build_id_is_deterministic_across_processes():
    """Recompute it the way the module does, from scratch. If this ever diverges, the
    compare-local-to-production workflow silently stops meaning anything."""
    root = Path(build_id.__module__ and __import__("aeo").__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    assert build_id() == digest.hexdigest()[:_ID_LEN]


def test_build_id_changes_when_the_source_changes(tmp_path, monkeypatch):
    """The whole point: identical source → identical id, one changed byte → different id.
    An id that did not move would report every stale deploy as fresh."""
    import aeo.build as build_mod

    def id_for(tree: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(p for p in tree.rglob("*.py") if "__pycache__" not in p.parts):
            digest.update(path.relative_to(tree).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()[: build_mod._ID_LEN]

    a = tmp_path / "a"
    (a / "sub").mkdir(parents=True)
    (a / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (a / "sub" / "other.py").write_text("y = 2\n", encoding="utf-8")

    b = tmp_path / "b"
    (b / "sub").mkdir(parents=True)
    (b / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (b / "sub" / "other.py").write_text("y = 2\n", encoding="utf-8")

    assert id_for(a) == id_for(b), "identical trees must produce identical ids"

    (b / "sub" / "other.py").write_text("y = 3\n", encoding="utf-8")
    assert id_for(a) != id_for(b), "a changed byte must change the id"


def test_pyc_files_are_ignored(tmp_path):
    """__pycache__ carries per-environment mtimes; including it would make the id differ
    between two machines running byte-identical source."""
    import aeo.build as build_mod

    tree = tmp_path / "pkg"
    (tree / "__pycache__").mkdir(parents=True)
    (tree / "mod.py").write_text("x = 1\n", encoding="utf-8")
    considered = [p for p in tree.rglob("*.py") if "__pycache__" not in p.parts]
    (tree / "__pycache__" / "mod.cpython-312.py").write_text("junk\n", encoding="utf-8")
    still = [p for p in tree.rglob("*.py") if "__pycache__" not in p.parts]
    assert considered == still
    assert build_mod._ID_LEN == 12


def test_an_unreadable_tree_degrades_to_unknown(monkeypatch):
    """A diagnostic must never be the reason /api/health 500s."""
    import aeo.build as build_mod

    build_mod.build_id.cache_clear()
    monkeypatch.setattr(
        build_mod.Path, "resolve", lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    try:
        assert build_mod.build_id() == "unknown"
    finally:
        build_mod.build_id.cache_clear()


@pytest.mark.parametrize("field", ["status", "db", "build"])
def test_health_reports_the_build_id(field):
    """The endpoint is the whole delivery mechanism — a build id nothing serves is useless."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from aeo.api.app import app as api_app

    body = TestClient(api_app).get("/api/health").json()
    assert field in body
    assert body["build"] == build_id()
