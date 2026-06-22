"""Rough token/cost estimation for the agent LLM router.

The backends in ``llm.py`` don't surface provider token usage, so the agent layer estimates
it (chars/4) and prices it from a small per-model table. Good enough for budgeting + whale
detection; swap in real ``usage`` capture later if exact billing is needed. All figures are
USD per 1,000 tokens (input, output)."""

from __future__ import annotations

# (input_per_1k, output_per_1k) in USD. Local models are free (self-hosted compute).
PRICES: dict[str, tuple[float, float]] = {
    "qwen2.5:3b": (0.0, 0.0),
    "gemini-2.5-flash": (0.0003, 0.0025),
}
_DEFAULT_PRICE = (0.0005, 0.0015)  # conservative fallback for an unknown model


def estimate_tokens(text: str) -> int:
    """A cheap, provider-agnostic token estimate (≈ 4 chars/token), floored at 1."""
    return max(1, len(text) // 4)


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """USD cost for a call, from the per-model price table (defaulting for unknown models)."""
    price_in, price_out = PRICES.get(model, _DEFAULT_PRICE)
    return round(tokens_in / 1000 * price_in + tokens_out / 1000 * price_out, 6)
