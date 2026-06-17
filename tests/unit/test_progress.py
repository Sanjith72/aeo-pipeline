"""Progressive results (#7) — the orchestrator's stage-progress emit helper.

The per-stage propagation through the job registry to a polling client is covered in
tests/unit/test_jobs.py and test_api.py; here we pin the emit primitive itself: it's
optional, best-effort, and never lets a broken sink break the run.
"""

from __future__ import annotations

from aeo.pipeline.orchestrator import RUN_STAGES, _emit


def test_emit_is_a_noop_without_a_sink():
    # No sink → no error, nothing happens.
    _emit(None, "discover", discovered=3)


def test_emit_forwards_stage_and_counts():
    seen: list[tuple[str, dict]] = []
    _emit(lambda stage, counts: seen.append((stage, counts)), "crawl", total=10, failed=1)
    assert seen == [("crawl", {"total": 10, "failed": 1})]


def test_emit_swallows_a_broken_sink():
    def boom(stage, counts):
        raise RuntimeError("sink exploded")

    # A broken progress sink must never propagate into the pipeline.
    _emit(boom, "analyze", analyzed=5)


def test_run_stages_contract():
    # The documented stage order a client can scaffold against. "profile" is the R2-2
    # homepage-first partial, surfaced right after discovery.
    assert RUN_STAGES == ("discover", "profile", "blueprint", "coverage", "crawl", "analyze", "report")
