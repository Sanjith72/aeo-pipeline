"""Which code is this process actually running?

The question this exists to answer: **did the Hugging Face factory rebuild pick up my
commit?** Backend deploys here are manual — a plain restart reuses the cached layer and
silently keeps the OLD code — and until now nothing could tell you which it did:

* ``/api/health`` returned ``{"status":"ok","db":"ok"}``, identical before and after a
  rebuild that changed nothing;
* the Spaces API's ``runtime.sha`` is the *Space wrapper repo's* commit, not this app's —
  the wrapper clones this repo at build time, so its SHA moves for reasons unrelated to the
  code aboard and stays still when this repo changes;
* and a fix whose only signature is an internal SQL ``WHERE`` clause has no behavioural
  probe at all.

So a "deployed" backend fix was unfalsifiable, which is exactly how one survives into
production wearing a green tick.

The identity used here is a **content hash of the installed package**, not a git SHA,
because a git SHA is not available: the image runs ``pip install "."``, so there is no
``.git`` in the container and no build arg the wrapper Dockerfile passes through. Hashing
what is actually importable needs nothing from any dashboard and cannot be faked by a
stale layer — if the running bytes differ, the hash differs.

Compare rather than decode. It is not a version number and does not order; it answers one
question, by equality:

    # local, on the commit you believe you deployed
    python -c "from aeo.build import build_id; print(build_id())"
    # what production is really running
    curl -s https://<api-host>/api/health

Equal → the Space is running that commit's code. Different → it is not, whatever the
dashboard says: factory rebuild (not restart) and check again.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

__all__ = ["build_id"]

#: Hex characters kept. 12 is ~48 bits — far beyond collision range for the handful of
#: builds anyone compares, and short enough to eyeball in a terminal.
_ID_LEN = 12


@lru_cache(maxsize=1)
def build_id() -> str:
    """A stable short hash of this package's Python source, or ``"unknown"``.

    Deterministic across machines and across the editable-install (``src/aeo``) vs
    site-packages copies, because only the package-relative path and the file BYTES go into
    the digest — never an absolute path, an mtime, or a directory listing order.

    Never raises. A build identity is a diagnostic; it must not be the reason a health check
    500s, so an unreadable tree degrades to ``"unknown"`` rather than failing.
    """
    try:
        root = Path(__file__).resolve().parent
        digest = hashlib.sha256()
        # Sorted, so filesystem enumeration order cannot change the result. __pycache__ is
        # excluded: .pyc files carry embedded mtimes and are regenerated per environment,
        # which would make the hash differ for identical source.
        for path in sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts):
            rel = path.relative_to(root).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()[:_ID_LEN]
    except Exception:
        return "unknown"
