"""Enable the $0 hybrid LLM (Gemini + Qwen) on the production Hugging Face Space.

WHY THIS EXISTS: the live Space (Sanjith12/aeo-api) was deployed with
``AEO__LLM__ENABLED=false`` and no provider keys. Every LLM-backed feature silently
degrades to deterministic output there — most visibly the wizard's competitor
recommendations, which come back "unavailable" for every business (the endpoint has
no non-LLM source of suggestions beyond mining the client site's own comparison
pages, which barely any small-business site has). This script flips the Space to the
hybrid Gemini+Qwen router that the codebase already ships (src/aeo/nlp/providers.py)
using free-tier keys, then restarts the Space.

Get the (free) keys first — both take ~2 minutes, no card:

  1. Gemini — https://aistudio.google.com/apikey — create the key in a Google Cloud
     project with NO billing account attached (that's what pins you to the free tier;
     note Google trains on free-tier data). Free quota: gemini-2.5-flash ~10 RPM /
     250 req-day, flash-lite ~15 RPM / 1000 req-day.
  2. Groq (serves Qwen) — https://console.groq.com — free plan, no card:
     qwen/qwen3-32b at 60 RPM / 1K req-day / 6K tokens-min.
  3. (Optional) OpenRouter — https://openrouter.ai/settings/keys — the ':free' Qwen
     fallback (20 RPM / 50 req-day) so a Groq outage doesn't take the family down.

DEPLOY ORDER — all three steps, in this order, or the fix is only half-live:

  1. **Commit and push to main first.** The Space image clones the GitHub repo at
     BUILD time (wrapper Dockerfile + GH_TOKEN build secret); this script triggers a
     factory rebuild, which deploys whatever main holds at that moment. Running it
     against an unpushed fix rebuilds the OLD backend.
  2. Run this script. It sets the env config AND factory-rebuilds the Space
     (restart alone would only re-run the existing image with the new env — the
     LLM would turn on but the old handler would keep answering without `reason`).
  3. Redeploy the Vercel web app (aeo-studio) so the picker understands the new
     `reason` field; an old bundle shows the generic copy for every blank.

Then run (any Python with huggingface_hub; the repo venv works):

    pip install huggingface_hub
    set HF_TOKEN=hf_...                # a WRITE token for the Space owner account
    set GEMINI_API_KEY=AIza...
    set GROQ_API_KEY=gsk_...
    python scripts/enable_space_llm.py

Verify after the Space finishes REBUILDING (several minutes — image build, not just
a process restart):

    curl -X POST https://sanjith12-aeo-api.hf.space/api/competitors/suggest \
      -H "Content-Type: application/json" -H "X-API-Key: <AEO__API__AUTH_KEY>" \
      -d '{"name":"Astreya","domain":"astreya.com","category":"IT managed services","location":"San Jose, CA"}'

    → expect "source": "llm" with real names, not "unavailable".
"""

from __future__ import annotations

import argparse
import os
import sys

SPACE_ID = "Sanjith12/aeo-api"

# Free Gemini is ~10-15 RPM; the deep audit's per-page analysis fan-out must not
# stampede it. 2 concurrent analysis calls keeps a 30-page audit under the limit
# (the hybrid router's 429 retry/failover absorbs the rest).
FREE_TIER_ANALYSIS_CONCURRENCY = "2"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--space", default=SPACE_ID, help=f"Space id (default {SPACE_ID})")
    ap.add_argument("--token", default=os.environ.get("HF_TOKEN"),
                    help="HF write token (default: $HF_TOKEN)")
    ap.add_argument("--gemini-key", default=os.environ.get("GEMINI_API_KEY"),
                    help="Gemini AI Studio key (default: $GEMINI_API_KEY)")
    ap.add_argument("--qwen-key", default=os.environ.get("GROQ_API_KEY"),
                    help="Groq API key for the Qwen side (default: $GROQ_API_KEY)")
    ap.add_argument("--qwen-fallback-key", default=os.environ.get("OPENROUTER_API_KEY"),
                    help="Optional OpenRouter ':free' fallback key (default: $OPENROUTER_API_KEY)")
    ap.add_argument("--no-restart", action="store_true",
                    help="Set config but skip the Space restart")
    args = ap.parse_args()

    missing = [flag for flag, val in [
        ("--token / HF_TOKEN", args.token),
        ("--gemini-key / GEMINI_API_KEY", args.gemini_key),
        ("--qwen-key / GROQ_API_KEY", args.qwen_key),
    ] if not val]
    if missing:
        print("Missing required credentials:\n  " + "\n  ".join(missing)
              + "\n\nSee the module docstring for where each free key comes from.")
        return 2

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("huggingface_hub is not installed — run: pip install huggingface_hub")
        return 2

    api = HfApi(token=args.token)

    # Public variables (visible in the Space UI) — the switch itself.
    variables = {
        "AEO__LLM__ENABLED": "true",
        "AEO__LLM__PROVIDER": "hybrid",
        # Pin the deep audit's ~100-300-call fan-out to the Qwen/Groq family so it
        # can't burn the small Gemini daily pools (flash ~250 req-day) that the
        # interactive paths — competitor suggest, /api/plan, personalize — fail over
        # to when Groq is busy. Without this, one demo-day audit spree can leave the
        # competitor picker in a llm_failed loop until quotas reset at midnight.
        "AEO__LLM__BULK_PROVIDER": "qwen",
        "AEO__VALIDATION__ANALYSIS_CONCURRENCY": FREE_TIER_ANALYSIS_CONCURRENCY,
    }
    # Secrets (write-only) — the provider keys.
    secrets = {
        "AEO__LLM__GEMINI_API_KEY": args.gemini_key,
        "AEO__LLM__QWEN_API_KEY": args.qwen_key,
    }
    if args.qwen_fallback_key:
        secrets["AEO__LLM__QWEN_FALLBACK_API_KEY"] = args.qwen_fallback_key

    for key, value in variables.items():
        api.add_space_variable(repo_id=args.space, key=key, value=value)
        print(f"variable set: {key}={value}")
    for key, value in secrets.items():
        api.add_space_secret(repo_id=args.space, key=key, value=value)
        print(f"secret set:   {key}=<hidden>")

    if args.no_restart:
        print("Config written. Factory-rebuild the Space from its settings page to apply\n"
              "(a plain restart re-runs the OLD image — env applies but code doesn't).")
        return 0
    # factory_reboot=True is load-bearing: the Space image clones the GitHub repo at
    # BUILD time, so only a rebuild picks up new backend code (the reason field, the
    # request budget). A plain restart would flip the LLM on while every failure path
    # kept answering with the old reason-less payload. Make sure main is pushed first.
    api.restart_space(repo_id=args.space, factory_reboot=True)
    print(f"Space {args.space} factory-rebuilding (several minutes; it clones current "
          "GitHub main — did you push?). Then run the verify curl from the module "
          "docstring, and redeploy the Vercel web app if it predates this fix.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
