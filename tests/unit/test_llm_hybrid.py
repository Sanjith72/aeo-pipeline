"""
Hybrid Gemini+Qwen router (nlp/providers.py): profile routing, 429/5xx retry with
backoff, cross-provider failover, malformed-JSON healing, and the embeddings surface.

All offline: httpx.Client is monkeypatched with a scripted fake (FIFO of canned
responses), and time.sleep is captured instead of slept.
"""

from __future__ import annotations

from typing import Any

from aeo.nlp import providers as providers_mod
from aeo.nlp.llm import LLMClient
from aeo.settings import LLMCfg


def _cfg(**overrides: Any) -> LLMCfg:
    base: dict[str, Any] = {
        "provider": "hybrid",
        "gemini_api_key": "g-key",
        "qwen_api_key": "q-key",
        "retry_max_attempts": 2,
        "retry_initial_backoff_sec": 0.5,
        "retry_max_backoff_sec": 2.0,
    }
    base.update(overrides)
    return LLMCfg(**base)


def _chat(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


class _Resp:
    def __init__(self, status: int = 200, payload: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _ScriptedClient:
    """Stand-in for httpx.Client: pops one canned response per POST, records calls."""

    def __init__(self, script: list[_Resp]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> _ScriptedClient:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def post(self, url: str, json: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None) -> _Resp:
        self.calls.append({"url": url, "json": json, "headers": headers})
        assert self.script, f"unexpected extra POST to {url}"
        return self.script.pop(0)


def _wire(monkeypatch, script: list[_Resp]) -> tuple[_ScriptedClient, list[float]]:
    fake = _ScriptedClient(script)
    monkeypatch.setattr(
        providers_mod.httpx, "Client", lambda timeout=None, transport=None: fake
    )
    slept: list[float] = []
    monkeypatch.setattr(providers_mod.time, "sleep", slept.append)
    return fake, slept


class TestProfileRouting:
    def test_fast_profile_runs_qwen_on_groq_first(self, monkeypatch):
        fake, _ = _wire(monkeypatch, [_Resp(200, _chat("fast answer"))])
        client = LLMClient(_cfg(), profile="fast")
        assert client.generate("Q?") == "fast answer"
        assert "api.groq.com" in fake.calls[0]["url"]
        assert fake.calls[0]["json"]["model"] == _cfg().qwen_model

    def test_reasoning_profile_runs_gemini_first(self, monkeypatch):
        fake, _ = _wire(monkeypatch, [_Resp(200, _chat("deep answer"))])
        client = LLMClient(_cfg(), profile="reasoning")
        assert client.generate("Q?") == "deep answer"
        assert "generativelanguage.googleapis.com" in fake.calls[0]["url"]
        assert fake.calls[0]["json"]["model"] == _cfg().gemini_model

    def test_fast_profile_spends_flash_lite_before_flash(self, monkeypatch):
        # In the fast lane Gemini joins the chain quota-first: flash-lite (≈4× the free
        # requests/day) before flash. Kill qwen with a 401 to expose the gemini order.
        fake, _ = _wire(monkeypatch, [_Resp(401), _Resp(200, _chat("lite"))])
        assert LLMClient(_cfg(), profile="fast").generate("Q?") == "lite"
        assert fake.calls[1]["json"]["model"] == _cfg().gemini_fallback_model

    def test_model_property_reports_the_primary_of_the_profile(self):
        assert LLMClient(_cfg(), profile="fast").model == _cfg().qwen_model
        assert LLMClient(_cfg(), profile="reasoning").model == _cfg().gemini_model

    def test_openrouter_fallback_gets_attribution_headers(self, monkeypatch):
        fake, _ = _wire(monkeypatch, [_Resp(200, _chat("ok"))])
        cfg = _cfg(qwen_api_key=None, qwen_fallback_api_key="or-key")
        LLMClient(cfg, profile="fast").generate("Q?")
        assert "openrouter.ai" in fake.calls[0]["url"]
        assert "HTTP-Referer" in fake.calls[0]["headers"]
        assert fake.calls[0]["headers"]["Authorization"] == "Bearer or-key"
        assert fake.calls[0]["json"]["model"] == cfg.qwen_fallback_model


class TestRetryAndFailover:
    def test_5xx_retries_then_fails_over_in_family_first(self, monkeypatch):
        # reasoning: flash 500s twice (retry budget = 2) → flash-LITE (in-family) answers.
        fake, slept = _wire(monkeypatch, [
            _Resp(500), _Resp(500), _Resp(200, _chat("saved by lite")),
        ])
        client = LLMClient(_cfg(), profile="reasoning")
        assert client.generate("Q?") == "saved by lite"
        assert len(fake.calls) == 3
        assert fake.calls[0]["json"]["model"] == _cfg().gemini_model
        assert fake.calls[1]["json"]["model"] == _cfg().gemini_model
        assert fake.calls[2]["json"]["model"] == _cfg().gemini_fallback_model
        assert len(slept) == 1  # one backoff between the two flash attempts

    def test_whole_family_down_crosses_to_the_other_family(self, monkeypatch):
        # reasoning: flash ×2 and flash-lite ×2 all 500 → qwen on groq answers.
        fake, _ = _wire(monkeypatch, [
            _Resp(500), _Resp(500), _Resp(500), _Resp(500),
            _Resp(200, _chat("saved by qwen")),
        ])
        client = LLMClient(_cfg(), profile="reasoning")
        assert client.generate("Q?") == "saved by qwen"
        assert "api.groq.com" in fake.calls[4]["url"]

    def test_429_honors_retry_after_within_the_cap(self, monkeypatch):
        fake, slept = _wire(monkeypatch, [
            _Resp(429, headers={"retry-after": "1.5"}), _Resp(200, _chat("ok")),
        ])
        client = LLMClient(_cfg(), profile="fast")
        assert client.generate("Q?") == "ok"
        assert len(fake.calls) == 2
        assert slept == [1.5]

    def test_429_with_a_long_retry_after_fails_over_immediately(self, monkeypatch):
        # Server demands a 60s wait > 2s cap → don't stall the worker; switch provider.
        fake, slept = _wire(monkeypatch, [
            _Resp(429, headers={"retry-after": "60"}), _Resp(200, _chat("ok")),
        ])
        client = LLMClient(_cfg(), profile="fast")
        assert client.generate("Q?") == "ok"
        assert len(fake.calls) == 2
        assert slept == []  # never slept — failover instead

    def test_every_backend_down_returns_none(self, monkeypatch):
        _wire(monkeypatch, [_Resp(500)] * 6)  # 2 attempts × 3 chain entries (qwen, lite, flash)
        client = LLMClient(_cfg(), profile="fast")
        assert client.generate("Q?") is None

    def test_failover_disabled_never_tries_the_second_backend(self, monkeypatch):
        fake, _ = _wire(monkeypatch, [_Resp(500), _Resp(500)])
        client = LLMClient(_cfg(failover_enabled=False), profile="fast")
        assert client.generate("Q?") is None
        assert all("api.groq.com" in c["url"] for c in fake.calls)


class TestJsonHealing:
    def test_malformed_json_is_healed_by_the_other_provider(self, monkeypatch):
        # qwen answers with truncated JSON → gemini is asked to repair the raw text.
        fake, _ = _wire(monkeypatch, [
            _Resp(200, _chat('{"score": 4, "reason": "unterminated')),
            _Resp(200, _chat('{"score": 4, "reason": "unterminated string fixed"}')),
        ])
        client = LLMClient(_cfg(), profile="fast")
        out = client.generate_json("Q?")
        assert out == {"score": 4, "reason": "unterminated string fixed"}
        assert "api.groq.com" in fake.calls[0]["url"]
        assert "generativelanguage" in fake.calls[1]["url"]
        assert "unterminated" in fake.calls[1]["json"]["messages"][-1]["content"]

    def test_hard_failure_falls_through_to_the_next_provider_for_json(self, monkeypatch):
        fake, _ = _wire(monkeypatch, [
            _Resp(401),  # qwen key rejected — terminal, no retry
            _Resp(200, _chat('{"ok": true}')),
        ])
        client = LLMClient(_cfg(), profile="fast")
        assert client.generate_json("Q?") == {"ok": True}
        assert len(fake.calls) == 2

    def test_json_valid_on_first_try_needs_no_healer(self, monkeypatch):
        fake, _ = _wire(monkeypatch, [_Resp(200, _chat('{"a": 1}'))])
        assert LLMClient(_cfg(), profile="fast").generate_json("Q?") == {"a": 1}
        assert len(fake.calls) == 1


class TestAvailability:
    def test_no_keys_means_disabled_and_deterministic(self):
        client = LLMClient(_cfg(gemini_api_key=None, qwen_api_key=None))
        assert client.enabled is False
        assert client.generate("Q?") is None
        assert client.generate_json("Q?") is None
        assert client.embed("text") is None

    def test_one_key_still_works_in_either_profile(self, monkeypatch):
        fake, _ = _wire(monkeypatch, [_Resp(200, _chat("gemini only"))])
        client = LLMClient(_cfg(qwen_api_key=None), profile="fast")
        assert client.enabled is True
        assert client.generate("Q?") == "gemini only"
        assert "generativelanguage" in fake.calls[0]["url"]

    def test_pinned_provider_without_its_key_is_disabled(self):
        assert LLMClient(LLMCfg(provider="qwen")).enabled is False
        assert LLMClient(LLMCfg(provider="gemini")).enabled is False


class TestEmbeddings:
    def test_embed_prefers_gemini_and_returns_768_floats(self, monkeypatch):
        vector = [0.25] * 768
        fake, _ = _wire(monkeypatch, [_Resp(200, {"data": [{"embedding": vector}]})])
        client = LLMClient(_cfg(), profile="fast")  # fast = qwen-first for CHAT only
        out = client.embed("some page text")
        assert out is not None and len(out) == 768
        assert "generativelanguage" in fake.calls[0]["url"]
        assert fake.calls[0]["url"].endswith("/embeddings")
        assert fake.calls[0]["json"]["dimensions"] == 768

    def test_embed_degrades_to_none_when_no_provider_serves_it(self, monkeypatch):
        _wire(monkeypatch, [_Resp(404), _Resp(404)])
        assert LLMClient(_cfg(), profile="fast").embed("text") is None

    def test_ollama_backend_has_no_embeddings_surface(self):
        assert LLMClient(LLMCfg(provider="ollama")).embed("text") is None
