"""
Config fingerprinting — pins the *measuring stick* to a blueprint version.

A blueprint already versions on its own inputs (topic, framework, competitors,
sitemap). But the gap analysis also measures against ``best_practices.yaml`` targets,
``scoring.yaml`` thresholds, and ``prioritization.yaml`` weights — none of which the
blueprint hash captured. Editing any of those between two runs silently changed the
score with no version bump, breaking week-over-week comparability.

``config_fingerprint()`` returns a short, deterministic hash of the *semantic*
content (parsed YAML, not raw bytes, so a comment edit doesn't churn the version) of
the config files that define the scoring contract. The generator folds it into the
blueprint's ``config_fingerprint``, which is part of ``Blueprint.hash_inputs()`` — so
a rubric/target/threshold change now bumps the blueprint version, exactly like a
structural change does.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from ..settings import load_yaml_file

# The config files that define the scoring/measurement contract. framework.yaml is
# included even though framework_version is already hashed — it catches edits that
# forget to bump the version.
_PINNED_CONFIGS = ("framework.yaml", "best_practices.yaml", "scoring.yaml", "prioritization.yaml")


@lru_cache(maxsize=1)
def config_fingerprint() -> str:
    """Deterministic 16-char hex fingerprint of the scoring-contract config files.

    Cached for the process lifetime (config is static per run). Missing files
    contribute an empty payload, so the fingerprint is stable across deployments
    that omit an optional file rather than raising."""
    payload = {name: load_yaml_file(name) for name in _PINNED_CONFIGS}
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
