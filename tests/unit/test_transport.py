"""
Unit tests for the force-IPv4 transport policy (v4 OCI Ampere infra).

The policy is a single seam: when ``crawler.force_ipv4`` is on, network clients
get an IPv4-bound httpx transport; otherwise they get None (httpx default). We
verify both branches for the sync and async transports.
"""

from __future__ import annotations

import httpx

from aeo.crawl import transport


class TestTransportPolicy:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr(transport, "force_ipv4_enabled", lambda: False)
        assert transport.sync_transport() is None
        assert transport.async_transport() is None

    def test_enabled_returns_ipv4_bound_transports(self, monkeypatch):
        monkeypatch.setattr(transport, "force_ipv4_enabled", lambda: True)
        sync = transport.sync_transport()
        asynct = transport.async_transport()
        assert isinstance(sync, httpx.HTTPTransport)
        assert isinstance(asynct, httpx.AsyncHTTPTransport)

    def test_reads_setting(self, monkeypatch):
        # force_ipv4_enabled is the one place that reads the setting.
        from aeo.settings import CrawlerCfg, Settings

        def fake_settings():
            s = Settings()
            s.crawler = CrawlerCfg(force_ipv4=True)
            return s

        monkeypatch.setattr(transport, "get_settings", fake_settings)
        assert transport.force_ipv4_enabled() is True
