"""Per-domain framework bootstrap — make AEO work for ANY website.

The generic skeleton must always be a valid, loadable framework; the LLM path must
tailor it and re-validate against the closed vocabulary (dropping invalid nodes)."""

from __future__ import annotations

from aeo.reference import framework as fw_mod
from aeo.reference.framework_bootstrap import (
    bootstrap_framework,
    generic_framework,
    write_framework,
)
from aeo.settings import get_settings


class FakeLLM:
    def __init__(self, payload, *, enabled=True, model="phi3"):
        self._payload = payload
        self.enabled = enabled
        self.model = model

    def generate_json(self, prompt, system=None):
        return self._payload


def test_generic_skeleton_shape():
    fw = generic_framework("acme-corp.com")
    assert fw["topic"] == "Acme Corp"          # derived from the domain
    assert fw["required_entities"] == []
    slugs = [n["slug"] for n in fw["standalone_nodes"]]
    assert "/product" in slugs and "/contact" in slugs and "/pricing" in slugs


def test_generic_framework_loads_as_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "config_dir", str(tmp_path))
    fw_mod.load_framework.cache_clear()
    try:
        write_framework("acme.com", bootstrap_framework("acme.com", llm=None))
        loaded = fw_mod.load_framework("acme.com")
        assert loaded.topic == "Acme"
        slugs = {n.slug for n in loaded.nodes}
        assert {"/", "/product", "/contact", "/resources", "/faq"} <= slugs
        assert any(c.name == "core-content" for c in loaded.clusters)
    finally:
        fw_mod.load_framework.cache_clear()


def test_no_llm_returns_generic():
    data = bootstrap_framework("acme.com", llm=None)
    assert "_generated_by" not in data
    assert data["topic"] == "Acme"


def test_llm_tailors_and_drops_invalid_vocab():
    payload = {
        "topic": "Payments",
        "required_entities": ["Stripe", "PCI DSS", "SCA", "Webhooks"],
        "clusters": [{
            "name": "payments",
            "min_pages": 6,
            "pillar": {"slug": "/what-is-online-payments", "title": "What Is Online Payments?",
                       "page_type": "pillar", "intent": "informational", "journey_stage": "awareness",
                       "required_entities": ["Stripe", "PCI DSS", "Bitcoin"],  # Bitcoin not in entity list -> filtered
                       "seed_questions": ["What is online payments?"]},
            "supporting": [
                {"slug": "/bad", "title": "Bad", "page_type": "megapage"},          # invalid vocab -> dropped
                {"slug": "/sca", "title": "Strong Customer Authentication", "page_type": "pillar",
                 "intent": "informational", "journey_stage": "consideration", "required_entities": ["SCA"]},
            ],
        }],
        "standalone_nodes": [
            {"slug": "/pricing", "title": "Pricing", "page_type": "product",
             "intent": "commercial", "journey_stage": "decision"},
        ],
    }
    data = bootstrap_framework("stripe.com", llm=FakeLLM(payload))
    assert data["topic"] == "Payments"
    assert "Stripe" in data["required_entities"]
    assert data["_generated_by"] == "bootstrap:phi3"

    cluster = data["clusters"][0]
    assert cluster["pillar"]["slug"] == "/what-is-online-payments"
    assert cluster["pillar"]["required_entities"] == ["Stripe", "PCI DSS"]  # Bitcoin filtered out
    sup_slugs = [s["slug"] for s in cluster["supporting"]]
    assert "/sca" in sup_slugs and "/bad" not in sup_slugs   # invalid page_type dropped


def test_llm_disabled_falls_back_to_generic():
    data = bootstrap_framework("acme.com", llm=FakeLLM({"topic": "X"}, enabled=False))
    assert "_generated_by" not in data  # disabled LLM -> generic


def test_llm_empty_output_falls_back_to_generic():
    data = bootstrap_framework("acme.com", llm=FakeLLM({"clusters": [], "standalone_nodes": []}))
    assert "_generated_by" not in data


def test_tailored_framework_loads_end_to_end(tmp_path, monkeypatch):
    payload = {
        "topic": "Payments",
        "required_entities": ["Stripe", "PCI DSS"],
        "clusters": [{"name": "pay", "min_pages": 5,
                      "pillar": {"slug": "/what-is-payments", "title": "What is payments",
                                 "page_type": "pillar", "intent": "informational",
                                 "journey_stage": "awareness", "required_entities": ["Stripe"]},
                      "supporting": []}],
        "standalone_nodes": [{"slug": "/pricing", "title": "Pricing", "page_type": "product",
                              "intent": "commercial", "journey_stage": "decision"}],
    }
    monkeypatch.setattr(get_settings(), "config_dir", str(tmp_path))
    fw_mod.load_framework.cache_clear()
    try:
        write_framework("stripe.com", bootstrap_framework("stripe.com", llm=FakeLLM(payload)))
        loaded = fw_mod.load_framework("stripe.com")
        assert loaded.topic == "Payments"
        assert "Stripe" in loaded.required_entities
        assert loaded.node_for_slug("/what-is-payments") is not None
    finally:
        fw_mod.load_framework.cache_clear()
