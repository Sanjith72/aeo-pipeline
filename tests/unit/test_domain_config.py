"""Per-domain onboarding config loader (config/domains/{domain}.yaml)."""

from __future__ import annotations

import pytest

from aeo.reference.domain_config import DomainConfig, load_domain_config, normalize_domain


class TestNormalizeDomain:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("https://www.Securin.io/blog", "securin.io"),
            ("securin.io/", "securin.io"),
            ("www.example.com", "example.com"),
            ("http://a.com", "a.com"),
            ("  Rapid7.com  ", "rapid7.com"),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert normalize_domain(raw) == expected


def test_missing_config_returns_none(tmp_path):
    assert load_domain_config("nope.com", config_dir=tmp_path) is None


def test_parses_full_config(tmp_path):
    (tmp_path / "domains").mkdir()
    (tmp_path / "domains" / "acme.com.yaml").write_text(
        "domain: acme.com\ntopic: PEV\nengine_target: perplexity\nmax_urls: 120\nlabel: w\n",
        encoding="utf-8",
    )
    dc = load_domain_config("https://www.acme.com/x", config_dir=tmp_path)
    assert isinstance(dc, DomainConfig)
    assert dc.domain == "acme.com"
    assert dc.topic == "PEV"
    assert dc.engine_target == "perplexity"
    assert dc.max_urls == 120
    assert dc.label == "w"


def test_unknown_engine_target_falls_back_to_none(tmp_path):
    (tmp_path / "domains").mkdir()
    (tmp_path / "domains" / "acme.com.yaml").write_text(
        "engine_target: bing_chat\n", encoding="utf-8"
    )
    dc = load_domain_config("acme.com", config_dir=tmp_path)
    assert dc is not None and dc.engine_target is None  # unknown → caller uses settings/generic


def test_bad_max_urls_is_ignored(tmp_path):
    (tmp_path / "domains").mkdir()
    (tmp_path / "domains" / "acme.com.yaml").write_text("max_urls: not-a-number\n", encoding="utf-8")
    dc = load_domain_config("acme.com", config_dir=tmp_path)
    assert dc is not None and dc.max_urls is None


def test_shipped_securin_config_loads():
    # The real onboarding file under config/domains/ resolves with the default dir.
    dc = load_domain_config("securin.io")
    assert dc is not None
    assert dc.topic == "PEV"
    assert dc.engine_target == "perplexity"
