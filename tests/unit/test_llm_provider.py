"""
Provider-agnostic LLM client tests.

The hybrid design ships two backends behind one ``LLMClient`` facade:
``ollama`` (local, default) and ``cloud`` (OpenAI-compatible HTTP). Selection
is by ``LLMCfg.provider``. These tests pin the dispatch logic, the
disabled-client contract every scorer relies on, and the exact request each
backend builds — all without touching the network (httpx is monkeypatched).
"""

from __future__ import annotations

from typing import Any, ClassVar

from aeo.nlp import llm as llm_mod
from aeo.nlp.llm import LLMClient
from aeo.settings import LLMCfg


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client: records the last POST, returns a canned body."""

    last: ClassVar[dict[str, Any]] = {}

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def post(self, url: str, json: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None) -> _FakeResponse:
        type(self).last = {"url": url, "json": json, "headers": headers}
        return _FakeResponse(self._payload)


def _patch_httpx(monkeypatch, payload: dict[str, Any]) -> None:
    fake = _FakeClient(payload)
    # Mirror the real call signature: the client now also receives transport=
    # (the force-IPv4 seam), which is None unless AEO__CRAWLER__FORCE_IPV4 is set.
    monkeypatch.setattr(llm_mod.httpx, "Client", lambda timeout=None, transport=None: fake)


class TestProviderSelection:
    def test_default_provider_is_ollama(self):
        client = LLMClient(LLMCfg())
        assert client.enabled is True
        assert client.provider == "ollama"
        assert client.model == "phi3"

    def test_cloud_provider_uses_cloud_model(self):
        client = LLMClient(LLMCfg(provider="cloud", cloud_model="gpt-4o-mini"))
        assert client.provider == "cloud"
        assert client.model == "gpt-4o-mini"

    def test_disabled_client_short_circuits(self):
        client = LLMClient(LLMCfg(enabled=False))
        assert client.enabled is False
        assert client.generate("anything") is None
        assert client.generate_json("anything") is None


class TestOllamaBackend:
    def test_builds_generate_request(self, monkeypatch):
        _patch_httpx(monkeypatch, {"response": "hello"})
        client = LLMClient(LLMCfg(provider="ollama", model="phi3"))
        out = client.generate("Q?", system="sys", json_mode=True)
        assert out == "hello"
        sent = _FakeClient.last
        assert sent["url"].endswith("/api/generate")
        assert sent["json"]["model"] == "phi3"
        assert sent["json"]["system"] == "sys"
        assert sent["json"]["format"] == "json"


class TestCloudBackend:
    def test_builds_openai_chat_request(self, monkeypatch):
        _patch_httpx(monkeypatch, {"choices": [{"message": {"content": "hi there"}}]})
        client = LLMClient(LLMCfg(
            provider="cloud",
            cloud_model="gpt-4o-mini",
            cloud_api_key="sk-test",
            cloud_base_url="https://api.openai.com/v1",
        ))
        out = client.generate("Q?", system="sys", json_mode=True)
        assert out == "hi there"
        sent = _FakeClient.last
        assert sent["url"].endswith("/chat/completions")
        assert sent["json"]["model"] == "gpt-4o-mini"
        assert sent["json"]["messages"][0] == {"role": "system", "content": "sys"}
        assert sent["json"]["messages"][1] == {"role": "user", "content": "Q?"}
        assert sent["json"]["response_format"] == {"type": "json_object"}
        assert sent["headers"]["Authorization"] == "Bearer sk-test"

    def test_generate_json_parses_object(self, monkeypatch):
        _patch_httpx(monkeypatch, {"choices": [{"message": {"content": '{"score": 4}'}}]})
        client = LLMClient(LLMCfg(provider="cloud"))
        assert client.generate_json("Q?") == {"score": 4}
