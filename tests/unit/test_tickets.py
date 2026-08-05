"""v5 CH-08/CH-15 tickets — offline API-surface + pure-helper checks (no Postgres, per the
offline test convention). The DB lifecycle (generate→close→verify→completed) is verified
separately against a live PG."""

from __future__ import annotations

import inspect


def test_ticket_repo_exposes_its_api() -> None:
    from aeo.storage.repos import milestones as m

    for fn in ("generate_tickets_from_run", "close_ticket", "reopen_ticket", "set_ticket_fields",
               "verify_tickets_by_recrawl", "list_tickets_for_run"):
        assert callable(getattr(m, fn))


def test_completed_pack_indices_exists() -> None:
    from aeo.storage.repos import packs

    assert callable(packs.completed_pack_indices)


def test_skills_with_findings_prefers_priorities() -> None:
    from aeo.storage.repos.milestones import _skills_with_findings

    detail = {"priorities": [{"skill": "messaging"}, {"skill": "conversion"}, {"skill": "messaging"}],
              "skills": {}}
    assert _skills_with_findings(detail) == ["messaging", "conversion"]  # deduped, ordered


def test_skills_with_findings_falls_back_to_suggestions() -> None:
    from aeo.storage.repos.milestones import _skills_with_findings

    detail = {"priorities": [],
              "skills": {"messaging": {"suggestions": [{"text": "x"}]}, "conversion": {"suggestions": []}}}
    assert _skills_with_findings(detail) == ["messaging"]


def test_url_path_helper() -> None:
    from aeo.storage.repos.milestones import _url_path

    assert _url_path("https://x.com/pricing") == "/pricing"
    assert _url_path("https://x.com/") == "x.com"  # bare-domain fallback for the homepage


def test_run_urls_accepts_force_recrawl() -> None:
    # CH-15: the close→verify re-crawl must bypass the fingerprint skip gate.
    from aeo.pipeline.orchestrator import Orchestrator

    assert "force_recrawl" in inspect.signature(Orchestrator.run_urls).parameters


def test_enqueue_batch_carries_force_recrawl() -> None:
    from aeo.pipeline import worker

    assert "force_recrawl" in inspect.signature(worker.enqueue_batch).parameters


# ── CH-02a: the ticket routes are gated like pack detail ──────────────────────────
# A ticket carries the same page×skill deep value as the gated pack detail, so an open
# ticket route makes the pack-detail 403 bypassable. Anonymous keeps Pack 1 (the free
# tier is unchanged); everything deeper needs a grant.

import pytest  # noqa: E402

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

RUN = 4242
TICKETS = [
    {"task_key": "t1", "pack_index": 1, "page_url": "https://x.com/", "status": "pending"},
    {"task_key": "t2", "pack_index": 2, "page_url": "https://x.com/pricing", "status": "pending"},
    {"task_key": "t3", "pack_index": 3, "page_url": "https://x.com/about", "status": "pending"},
]


@pytest.fixture
def tickets_api(monkeypatch):
    """A TestClient over the ticket routes with every repo stubbed (no Postgres)."""
    from types import SimpleNamespace

    from aeo.api import app as app_mod
    from aeo.storage.repos import entitlements as ent_repo
    from aeo.storage.repos import milestones as m_repo
    from aeo.storage.repos import packs as packs_repo
    from aeo.storage.repos import runs as runs_repo
    from aeo.storage.repos import targets as targets_repo

    monkeypatch.setattr(runs_repo, "domain_for_run", lambda run_id: "x.com")
    monkeypatch.setattr(targets_repo, "by_domain", lambda d: SimpleNamespace(id=7, name="x.com"))
    monkeypatch.setattr(packs_repo, "completed_pack_indices", lambda run_id: [])
    monkeypatch.setattr(ent_repo, "list_for_user_domain", lambda uid, d: [])
    monkeypatch.setattr(
        m_repo, "list_tickets_for_run",
        lambda cid, pack_index=None: [t for t in TICKETS if pack_index in (None, t["pack_index"])],
    )
    monkeypatch.setattr(
        m_repo, "get_ticket", lambda cid, key: next((t for t in TICKETS if t["task_key"] == key), None)
    )
    # Unowned by default (the anonymous free-tier board). MUST be stubbed here, not only in
    # owned_api: the mutation routes call it, so leaving it live makes these tests pass only
    # on a machine with a reachable Postgres — and CI runs the unit job WITHOUT one.
    monkeypatch.setattr(m_repo, "pack_owner_of", lambda cid: None)
    # Same reasoning: the read AND mutation routes now try to CLAIM an unowned board, so an
    # unstubbed claim would reach Postgres and make every test here machine-dependent.
    monkeypatch.setattr(m_repo, "claim_pack_owner", lambda cid, uid: uid)
    monkeypatch.setattr(m_repo, "close_ticket", lambda cid, key, **k: dict(TICKETS[0]))
    monkeypatch.setattr(m_repo, "reopen_ticket", lambda cid, key: dict(TICKETS[0]))
    monkeypatch.setattr(m_repo, "set_ticket_fields", lambda cid, key, **k: dict(TICKETS[0]))
    monkeypatch.setattr("aeo.pipeline.worker.enqueue_batch", lambda *a, **k: 99)
    return TestClient(app_mod.app), app_mod


def _grant_all(monkeypatch, app_mod):
    """Simulate a viewer holding an all_packs entitlement (the agency override)."""
    monkeypatch.setattr(app_mod, "_grants_for", lambda user, run_id: [{"scope": "all_packs"}])


def test_ticket_list_filters_to_unlocked_packs(tickets_api) -> None:
    client, _ = tickets_api
    body = client.get(f"/api/tickets/{RUN}").json()
    assert [t["pack_index"] for t in body["tickets"]] == [1]  # Pack 1 free, deeper withheld
    assert body["locked_ticket_count"] == 2  # counted, never leaked


def test_ticket_list_unfiltered_with_all_packs_grant(tickets_api, monkeypatch) -> None:
    client, app_mod = tickets_api
    _grant_all(monkeypatch, app_mod)
    body = client.get(f"/api/tickets/{RUN}").json()
    assert [t["pack_index"] for t in body["tickets"]] == [1, 2, 3]
    assert body["locked_ticket_count"] == 0


def test_pack_tickets_403_when_locked(tickets_api) -> None:
    client, _ = tickets_api
    assert client.get(f"/api/tickets/{RUN}/1").status_code == 200  # Pack 1 stays free
    assert client.get(f"/api/tickets/{RUN}/2").status_code == 403


def test_pack_tickets_200_when_granted(tickets_api, monkeypatch) -> None:
    client, app_mod = tickets_api
    _grant_all(monkeypatch, app_mod)
    assert client.get(f"/api/tickets/{RUN}/2").status_code == 200


@pytest.mark.parametrize("route", ["close", "reopen", "recheck"])
def test_ticket_mutations_403_on_locked_pack(tickets_api, route) -> None:
    """Closing burns a real crawl AND drives progressive unlock — an open route would let
    anyone earn their way into paid packs for free."""
    client, _ = tickets_api
    res = client.post(f"/api/tickets/{RUN}/{route}", json={"task_key": "t2"})
    assert res.status_code == 403


def test_ticket_fields_403_on_locked_pack(tickets_api) -> None:
    client, _ = tickets_api
    res = client.post(
        f"/api/tickets/{RUN}/fields", json={"task_key": "t2", "set_assignee": True, "assignee": "me"}
    )
    assert res.status_code == 403


def test_ticket_close_allowed_on_free_pack_1(tickets_api) -> None:
    client, _ = tickets_api
    assert client.post(f"/api/tickets/{RUN}/close", json={"task_key": "t1"}).status_code == 200


# ── P5: per-user ownership on ticket MUTATIONS ────────────────────────────────────
# Migration 0031 stamped owner_user_id but nothing enforced it, so any logged-in user with
# an entitlement on the domain could close another user's tickets — and closing spends
# crawl budget AND drives progressive unlock.


@pytest.fixture
def owned_api(tickets_api, monkeypatch):
    """The ticket board is owned by 'owner-1'."""
    from aeo.storage.repos import milestones as m_repo

    client, app_mod = tickets_api
    monkeypatch.setattr(m_repo, "pack_owner_of", lambda cid: "owner-1")
    return client, app_mod


def _as_user(monkeypatch, app_mod, user_id: str | None):
    """Force the request's resolved user (bypasses JWT plumbing).

    The override KEY must be the exact function object FastAPI registered at import time —
    ``app_mod.get_optional_user``. Monkeypatching the attribute first and then using it as
    the key silently no-ops, because the key would be the replacement, not the original."""
    from types import SimpleNamespace

    user = None if user_id is None else SimpleNamespace(id=user_id, email=None, role="authenticated")
    app_mod.app.dependency_overrides[app_mod.get_optional_user] = lambda: user


def test_unowned_board_stays_open_for_anonymous(tickets_api, monkeypatch):
    """The signed-out free Pack-1 flow must keep working — enforcing on an unowned board
    would break the anonymous experience the free tier depends on."""
    from aeo.storage.repos import milestones as m_repo

    client, _ = tickets_api
    monkeypatch.setattr(m_repo, "pack_owner_of", lambda cid: None)
    assert client.post(f"/api/tickets/{RUN}/close", json={"task_key": "t1"}).status_code == 200


def test_owner_can_close_their_own_ticket(owned_api, monkeypatch):
    client, app_mod = owned_api
    _as_user(monkeypatch, app_mod, "owner-1")
    try:
        assert client.post(f"/api/tickets/{RUN}/close", json={"task_key": "t1"}).status_code == 200
    finally:
        app_mod.app.dependency_overrides.clear()


def test_a_different_user_cannot_close_someone_elses_ticket(owned_api, monkeypatch):
    client, app_mod = owned_api
    _as_user(monkeypatch, app_mod, "intruder-2")
    try:
        res = client.post(f"/api/tickets/{RUN}/close", json={"task_key": "t1"})
        assert res.status_code == 403
    finally:
        app_mod.app.dependency_overrides.clear()


def test_anonymous_cannot_touch_an_owned_board(owned_api, monkeypatch):
    client, app_mod = owned_api
    _as_user(monkeypatch, app_mod, None)
    try:
        assert client.post(f"/api/tickets/{RUN}/close", json={"task_key": "t1"}).status_code == 403
    finally:
        app_mod.app.dependency_overrides.clear()


def test_all_packs_holder_keeps_the_agency_override(owned_api, monkeypatch):
    """§9.2's advanced/agency override must still reach an owned board — otherwise the
    override that exists precisely to skip the earn-forward path is defeated."""
    from aeo.storage.repos import entitlements as ent_repo

    client, app_mod = owned_api
    _as_user(monkeypatch, app_mod, "agency-9")
    monkeypatch.setattr(ent_repo, "list_for_user_domain", lambda uid, d: [{"scope": "all_packs"}])
    try:
        assert client.post(f"/api/tickets/{RUN}/close", json={"task_key": "t1"}).status_code == 200
    finally:
        app_mod.app.dependency_overrides.clear()


@pytest.mark.parametrize("route", ["reopen", "recheck", "fields"])
def test_every_mutation_is_ownership_gated(owned_api, monkeypatch, route):
    client, app_mod = owned_api
    _as_user(monkeypatch, app_mod, "intruder-2")
    body = {"task_key": "t1"}
    if route == "fields":
        body |= {"set_assignee": True, "assignee": "me"}
    try:
        assert client.post(f"/api/tickets/{RUN}/{route}", json=body).status_code == 403
    finally:
        app_mod.app.dependency_overrides.clear()


# ── P5: ownership must actually be STAMPED, not merely enforced ───────────────────
# The tests above prove the gate works GIVEN an owner. In production there never was one:
# boards are generated by the pipeline (orchestrator._generate_tickets), which passed no
# owner, and the only stamping site was inside GET /api/tickets/{run_id} on LAZY generation
# — a branch that runs only when the board does not exist yet. So pack_owner_of() returned
# None for every real board, _require_ticket_owner took its "unowned → open" path, and the
# whole gate was inert. These cover the two places ownership now comes from.


@pytest.fixture
def claims(monkeypatch):
    """Record every claim_pack_owner(client_id, user_id) the request makes."""
    from aeo.storage.repos import milestones as m_repo

    seen: list[tuple[int, str]] = []

    def _claim(cid, uid):
        seen.append((cid, uid))
        return uid

    monkeypatch.setattr(m_repo, "claim_pack_owner", _claim)
    return seen


def test_authenticated_read_claims_an_unowned_board(tickets_api, claims, monkeypatch):
    """The route the UI reads a pack's fixes from (web/lib/api.ts::getPackTickets ←
    quest/PackPlanSection.tsx). The run-wide route is fetched only for its
    locked_ticket_count, so claiming solely there would leave a board unowned for any user
    whose plan never needed that count."""
    client, app_mod = tickets_api
    _as_user(monkeypatch, app_mod, "user-a")
    try:
        assert client.get(f"/api/tickets/{RUN}/1").status_code == 200
    finally:
        app_mod.app.dependency_overrides.clear()
    assert claims == [(7, "user-a")], "first authenticated read must claim the board"


def test_run_wide_read_also_claims(tickets_api, claims, monkeypatch):
    client, app_mod = tickets_api
    _as_user(monkeypatch, app_mod, "user-a")
    try:
        assert client.get(f"/api/tickets/{RUN}").status_code == 200
    finally:
        app_mod.app.dependency_overrides.clear()
    assert claims == [(7, "user-a")]


def test_anonymous_read_never_claims(tickets_api, claims, monkeypatch):
    """The free signed-out tier must leave the board unowned and open."""
    client, app_mod = tickets_api
    _as_user(monkeypatch, app_mod, None)
    try:
        assert client.get(f"/api/tickets/{RUN}/1").status_code == 200
    finally:
        app_mod.app.dependency_overrides.clear()
    assert claims == []


def test_an_already_owned_board_is_not_reclaimed(tickets_api, claims, monkeypatch):
    """Ownership never transfers. A second user reading Pack 1 (free to everyone) must not
    overwrite the owner — that would hand any stranger the board with a single GET."""
    from aeo.storage.repos import milestones as m_repo

    client, app_mod = tickets_api
    monkeypatch.setattr(m_repo, "pack_owner_of", lambda cid: "owner-1")
    _as_user(monkeypatch, app_mod, "intruder-2")
    try:
        assert client.get(f"/api/tickets/{RUN}/1").status_code == 200
    finally:
        app_mod.app.dependency_overrides.clear()
    assert claims == []


def test_claim_failure_never_breaks_the_read(tickets_api, monkeypatch):
    """Claiming is bookkeeping. A lost race or a missing app_users row must not 500 a read."""
    from aeo.storage.repos import milestones as m_repo

    def _boom(cid, uid):
        raise RuntimeError("FK violation: app_users row missing")

    client, app_mod = tickets_api
    monkeypatch.setattr(m_repo, "claim_pack_owner", _boom)
    _as_user(monkeypatch, app_mod, "user-a")
    try:
        assert client.get(f"/api/tickets/{RUN}/1").status_code == 200
    finally:
        app_mod.app.dependency_overrides.clear()


@pytest.fixture
def audit_api(monkeypatch):
    """POST /api/audit with the parts that are not under test neutralised: the SSRF guard
    (which would otherwise make the result depend on DNS) and the real spawn. Returns the
    kwargs spawn_audit was called with."""
    from aeo.api import app as app_mod
    from aeo.api import jobs as jobs_mod

    seen: dict = {}
    monkeypatch.setattr(app_mod, "_assert_crawlable_host", lambda d, **k: None)
    monkeypatch.setattr(jobs_mod, "spawn_audit", lambda job_id, **kw: seen.update(kw) or None)
    # The registry is process-global and its dedupe returns any in-flight job for the same
    # domain WITHOUT spawning. Clear it on the way IN so a stale job can't empty `seen`, and
    # on the way OUT because these tests stub the spawn — the job they create never
    # progresses, so leaving it behind would make the next test that audits this domain
    # dedupe onto a job that can never finish. Test order would then decide the result.
    jobs_mod.JOBS._jobs.clear()
    yield TestClient(app_mod.app), app_mod, seen
    jobs_mod.JOBS._jobs.clear()


def test_starting_an_audit_stamps_the_board_with_the_caller(audit_api, monkeypatch):
    """The primary ownership source: whoever STARTS the audit owns the board it produces.
    Without this the board is born unowned and the first reader claims it — so a signed-in
    customer's board would be up for grabs until they happened to open it."""
    client, app_mod, seen = audit_api
    _as_user(monkeypatch, app_mod, "buyer-7")
    try:
        assert client.post("/api/audit", json={"domain": "acme.com"}).status_code == 200
    finally:
        app_mod.app.dependency_overrides.clear()
    assert seen["owner_user_id"] == "buyer-7"


def test_an_anonymous_audit_stays_unowned(audit_api, monkeypatch):
    """The free tier: no user, no owner, board stays open. Unchanged behaviour."""
    client, app_mod, seen = audit_api
    _as_user(monkeypatch, app_mod, None)
    try:
        assert client.post("/api/audit", json={"domain": "acme.com"}).status_code == 200
    finally:
        app_mod.app.dependency_overrides.clear()
    assert seen["owner_user_id"] is None


def test_the_pipeline_passes_the_owner_through_to_generation(monkeypatch):
    """The last link in the chain: audit_cycle -> _generate_tickets -> the repo. This used
    to hard-code no owner, with a comment explaining that a later view would stamp it —
    the assumption that made the gate inert."""
    from aeo.pipeline.orchestrator import Orchestrator
    from aeo.storage.repos import milestones as m_repo

    seen: dict = {}
    monkeypatch.setattr(
        m_repo, "generate_tickets_from_run",
        lambda run_id, **kw: seen.update(run_id=run_id, **kw) or {"tickets": 0, "packs": 0},
    )
    Orchestrator.__new__(Orchestrator)._generate_tickets(
        99, domain="acme.com", owner_user_id="buyer-7"
    )
    assert seen == {"run_id": 99, "owner_user_id": "buyer-7"}
