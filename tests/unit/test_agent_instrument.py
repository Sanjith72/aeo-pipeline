"""InstrumentedLLM: duck-types LLMClient and records a CallTrace per call."""

from __future__ import annotations


class FakeInner:
    enabled = True
    model = "gemini-2.5-flash"

    def generate(self, prompt, system=None, *, json_mode=False):
        return "some output"

    def generate_json(self, prompt, system=None):
        return {"ok": True}


def test_records_a_call_and_passes_the_result_through() -> None:
    from aeo.agents.instrument import InstrumentedLLM

    inst = InstrumentedLLM(FakeInner())
    assert inst.enabled is True
    assert inst.model == "gemini-2.5-flash"

    out = inst.generate_json("a prompt that is reasonably long", "system text")
    assert out == {"ok": True}
    assert len(inst.calls) == 1
    call = inst.calls[0]
    assert call.model == "gemini-2.5-flash"
    assert call.tokens > 0
    assert call.cost_usd > 0
    assert call.latency_ms >= 0


def test_delegates_enabled_to_inner() -> None:
    from aeo.agents.instrument import InstrumentedLLM

    class Disabled:
        enabled = False
        model = "qwen2.5:3b"

        def generate_json(self, prompt, system=None):
            return None

    inst = InstrumentedLLM(Disabled())
    assert inst.enabled is False
    assert inst.generate_json("x") is None
    assert len(inst.calls) == 1  # the (null) attempt is still recorded
