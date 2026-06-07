"""Config pinning: the scoring-contract config files are folded into the blueprint
version hash, so editing the measuring stick bumps the version (week-over-week
comparability)."""

from __future__ import annotations

from aeo.reference import Blueprint, SitemapNode
from aeo.reference.config_pin import config_fingerprint
from aeo.reference.generator import generate_blueprint


def _bp(**kw) -> Blueprint:
    return Blueprint(topic="PEV", sitemap=[SitemapNode(slug="/a", title="A")], **kw)


def test_fingerprint_is_stable_16_char_hex():
    fp = config_fingerprint()
    assert isinstance(fp, str) and len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)
    assert config_fingerprint() == fp  # cached / deterministic


def test_config_fingerprint_changes_blueprint_hash():
    a = _bp(config_fingerprint="aaaa")
    b = _bp(config_fingerprint="bbbb")
    assert a.hash_inputs() != b.hash_inputs()


def test_empty_fingerprint_is_default_and_neutral():
    # Direct construction (outside the generator) leaves the field empty, so the
    # legacy hash contract is preserved.
    assert _bp().config_fingerprint == ""


def test_generator_sets_config_fingerprint():
    bp = generate_blueprint(llm=None)
    assert bp.config_fingerprint == config_fingerprint()
    assert bp.config_fingerprint  # non-empty
    # and it is part of the content hash
    assert bp.content_hash == bp.hash_inputs()
