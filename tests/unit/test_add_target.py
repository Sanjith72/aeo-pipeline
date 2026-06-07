"""`aeo add-target` — register any website as a client/competitor (offline: the repo
write is mocked; the DB round-trip is covered by the gated integration suite)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aeo.cli import app
from aeo.storage.models import Target
from aeo.storage.repos import targets as targets_repo

runner = CliRunner()


class TestTargetHost:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("acme.com", "acme.com"),
            ("https://www.Acme.com/", "acme.com"),
            ("http://acme.com/path", "acme.com"),
            ("WWW.Example.IO", "example.io"),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert targets_repo.target_host(raw) == expected


def test_add_target_client_invokes_upsert(monkeypatch):
    captured = {}

    def fake_upsert(name, domain, kind="client", *, website_url=None):
        captured.update(name=name, domain=domain, kind=kind, website_url=website_url)
        return Target(id=7, name=name, domain=targets_repo.target_host(domain), kind=kind)

    monkeypatch.setattr(targets_repo, "upsert", fake_upsert)
    result = runner.invoke(app, ["add-target", "Acme", "https://www.acme.com/"])
    assert result.exit_code == 0
    assert captured["kind"] == "client"
    assert "client target ready: Acme (acme.com) id=7" in result.stdout
    assert "audit-cycle acme.com -t Acme" in result.stdout


def test_add_target_competitor_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        targets_repo, "upsert",
        lambda name, domain, kind="client", *, website_url=None: (
            seen.update(kind=kind) or Target(id=1, name=name, domain=domain, kind=kind)
        ),
    )
    result = runner.invoke(app, ["add-target", "Rival", "rival.io", "--competitor"])
    assert result.exit_code == 0
    assert seen["kind"] == "competitor"
