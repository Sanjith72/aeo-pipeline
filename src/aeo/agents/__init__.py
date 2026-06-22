"""Phase 2 agent runtime: a deterministic controller that wraps the existing engines as
tools and stages proposals for human approval. See docs/superpowers/specs/2026-06-22-*."""

from .planner import plan_tasks

__all__ = ["plan_tasks"]
