"""Competitor discovery prompt — strictly centers the specific industry + location."""

from __future__ import annotations

import threading
import time

from aeo.reference.competitor_discovery import _discovery_prompt, discover_competitors


def test_prompt_requires_same_industry_and_location():
    p = _discovery_prompt("Acme", "acme.com", "Healthcare", "Austin, TX", 5)
    assert "Healthcare industry" in p
    assert "Austin, TX" in p
    assert "SAME industry (Healthcare)" in p
    assert "SAME market (Austin, TX)" in p


def test_prompt_includes_services_when_present():
    p = _discovery_prompt("Acme", "acme.com", "Finance", None, 5, services=["Mortgage lending"])
    assert "Mortgage lending" in p
    assert "SAME industry (Finance)" in p
    assert "market" not in p.split("MUST", 1)[-1]  # no location constraint when location is None


def test_relaxed_prompt_drops_hard_constraint():
    p = _discovery_prompt("Acme", "acme.com", "Healthcare", "Austin, TX", 5, strict=False)
    # Industry/location still appear as soft context, but the hard "MUST" clause is gone.
    assert "Healthcare industry" in p
    assert "Every competitor MUST" not in p


def test_discover_competitors_no_llm_is_empty():
    # No LLM → empty result, never raises (onboarding proceeds by hand).
    result = discover_competitors("Acme", "acme.com", topic="Healthcare", location="Austin")
    assert result.verified == []
    assert result.llm_ok is False  # the LLM never ran — callers must not read this as "no peers exist"


class _FakeLLM:
    """Minimal LLM stub. ``answer_when(predicate, competitors)`` rules fire by prompt text;
    every prompt is recorded so tests can assert which relaxation passes ran."""

    enabled = True

    def __init__(self, rules):
        self._rules = rules
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, system: str) -> dict:
        self.prompts.append(prompt)
        for predicate, competitors in self._rules:
            if predicate(prompt):
                return {"competitors": competitors}
        return {"competitors": []}


def _comp(name: str, domain: str) -> dict:
    return {"name": name, "domain": domain, "aliases": []}


def test_relaxes_location_when_strict_pass_is_empty():
    # Strict city pass returns nothing; the broadened (state) pass supplies peers.
    llm = _FakeLLM([
        (lambda p: "SAME market (TX)" in p, [_comp("Beta Health", "beta.com")]),
    ])
    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=llm, head_check=lambda _d: True,
    )
    assert [c.name for c in result.verified] == ["Beta Health"]
    assert result.relaxed is True
    # The very first pass was the strict city ask.
    assert "SAME market (Austin, TX)" in llm.prompts[0]


def test_softens_industry_as_last_resort():
    # Nothing matches until the hard-constraint clause is dropped entirely.
    llm = _FakeLLM([
        (lambda p: "Every competitor MUST" not in p, [_comp("Gamma", "gamma.com")]),
    ])
    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=llm, head_check=lambda _d: True,
    )
    assert [c.name for c in result.verified] == ["Gamma"]
    assert result.relaxed is True


def test_strict_pass_wins_without_relaxing():
    llm = _FakeLLM([
        (lambda p: "SAME market (Austin, TX)" in p, [_comp("Delta", "delta.com")]),
    ])
    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=llm, head_check=lambda _d: True,
    )
    assert [c.name for c in result.verified] == ["Delta"]
    assert result.relaxed is False
    assert len(llm.prompts) == 1  # stopped at the strict pass, no wasted calls
    # The early-return path must carry llm_ok too — verified non-empty implies llm_ok
    # (a refactor that reconstructs this result and forgets the flag must fail here).
    assert result.llm_ok is True


def test_ladder_stops_at_first_verified_pass_never_fires_four_calls():
    # The strict pass already verifies a peer → exactly ONE llm.generate_json call, even
    # though four relaxation rungs are available. Guards against the slow 4×-LLM walk.
    llm = _FakeLLM([(lambda p: True, [_comp(f"C{i}", f"c{i}.com") for i in range(5)])])
    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        count=5, llm=llm, head_check=lambda _d: True,
    )
    assert len(result.verified) == 5
    assert len(llm.prompts) == 1  # never walked the rest of the ladder


def test_llm_ok_true_when_llm_answers_but_proposes_nothing():
    # Every ladder pass gets a well-formed-but-empty answer → llm_ok True: the model
    # works, the blank is real ("no_results"), not a failure to retry.
    llm = _FakeLLM([])
    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=llm, head_check=lambda _d: True,
    )
    assert result.verified == []
    assert result.llm_ok is True


def test_llm_ok_false_when_every_call_raises():
    class _RaisingLLM:
        enabled = True

        def generate_json(self, prompt: str, system: str) -> dict:
            raise RuntimeError("provider down")

    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=_RaisingLLM(), head_check=lambda _d: True,
    )
    assert result.verified == []
    assert result.llm_ok is False  # nothing usable ever came back → transient failure, not an honest blank


def test_llm_ok_true_when_a_later_pass_answers():
    # First (strict) pass fails outright; a relaxed pass answers with an empty list.
    # One usable answer anywhere on the ladder → llm_ok True.
    class _FlakyLLM:
        enabled = True

        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt: str, system: str) -> dict:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("rate limited")
            return {"competitors": []}

    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=_FlakyLLM(), head_check=lambda _d: True,
    )
    assert result.verified == []
    assert result.llm_ok is True


def test_non_list_competitors_shape_never_raises_and_is_not_ok():
    # The model controls the inner shape: a single object instead of a list (classic
    # small-model JSON-mode drift), null, or a bare {} must be treated as an unusable
    # reply — never sliced (TypeError → endpoint 500) and never counted as an answer.
    class _BadShapeLLM:
        enabled = True

        def __init__(self, payload):
            self._payload = payload

        def generate_json(self, prompt: str, system: str) -> dict:
            return self._payload

    for payload in (
        {"competitors": {"name": "Rapid7", "domain": "rapid7.com"}},
        {"competitors": True},
        {"competitors": None},
        {},
    ):
        result = discover_competitors(
            "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
            llm=_BadShapeLLM(payload), head_check=lambda _d: True,
        )
        assert result.verified == []
        assert result.llm_ok is False, f"payload {payload!r} must not count as usable"


def test_all_candidates_dropped_by_verification_keeps_llm_ok():
    # The LLM proposes real candidates on every pass but live probes reject them all
    # (WAF'd datacenter egress, dead domains). The LLM WORKED — llm_ok must stay True
    # and raw_count must surface the proposals, so the API can say "verification
    # failed", never "no peers exist" and never the retry-nudging llm_failed.
    llm = _FakeLLM([(lambda p: True, [_comp("X", "x.com")])])
    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=llm, head_check=lambda _d: False,
    )
    assert result.verified == []
    assert result.llm_ok is True
    assert result.raw_count > 0
    assert [c.name for c in result.dropped] == ["X"]


def test_domain_verification_runs_concurrently():
    # Inject a HeadCheck that records peak concurrency: a sequential loop would never see
    # more than one probe in flight; the parallel pool should overlap them.
    comps = [_comp(f"C{i}", f"c{i}.com") for i in range(5)]
    llm = _FakeLLM([(lambda p: True, comps)])

    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow_check(domain: str) -> bool:
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)  # hold the slot so overlapping probes are observable
        with lock:
            state["now"] -= 1
        return True

    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        count=5, llm=llm, head_check=slow_check,
    )
    assert len(result.verified) == 5
    assert state["max"] >= 2  # probes overlapped — not verified one-at-a-time
