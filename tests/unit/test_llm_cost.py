"""Token/cost estimation for the agent LLM router."""

from __future__ import annotations

from aeo.nlp.cost import estimate_cost, estimate_tokens


def test_estimate_tokens_is_roughly_chars_over_four() -> None:
    assert estimate_tokens("a" * 40) == 10
    assert estimate_tokens("") == 1  # floor at 1 so a call never reads as 0 tokens


def test_local_model_is_free() -> None:
    assert estimate_cost("qwen2.5:3b", 1000, 1000) == 0.0


def test_cloud_model_costs_more_for_output() -> None:
    cost = estimate_cost("gemini-2.5-flash", 1000, 1000)
    assert cost > 0
    # output is priced higher than input, so output-heavy calls cost more
    assert estimate_cost("gemini-2.5-flash", 0, 1000) > estimate_cost("gemini-2.5-flash", 1000, 0)


def test_unknown_model_uses_a_default_price() -> None:
    assert estimate_cost("some-new-model", 1000, 1000) > 0
