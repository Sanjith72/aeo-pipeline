"""DEPLOY.md must not drift from settings.py.

Phase 6 item 18 found four `AEO__PAYMENTS__*` settings that exist in code and appear nowhere
in the deploy documentation, months after a phase whose stated job was to complete that table.
Documentation drift is not self-announcing — nothing fails, an operator simply cannot discover
a knob, or trusts a row that describes a variable the code has never read. So the invariant
gets a test instead of another manual audit.

Scope is deliberately narrow. Requiring all 133 settings to be documented would be noise: most
are tuning knobs with sane defaults that belong in .env.example, not a deploy runbook. Two
things are asserted, and they are the two the item actually asks for:

  1. Every AEO__PAYMENTS__* variable is documented. This is the money path — an undocumented
     switch here is how someone ends up unsetting a credential to achieve something a flag
     already does.
  2. Every AEO__* variable the document MENTIONS actually exists. A row for a variable the
     code never reads is worse than no row: it is followed, has no effect, and the reader
     concludes the software is broken. DEPLOY.md carried exactly such a row until Phase 4.2
     (`NEXT_PUBLIC_API_BASE`, which existed nowhere in the repo).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from aeo.settings import Settings

DEPLOY_MD = Path("DEPLOY.md")


def _env_names(model: type[BaseModel], prefix: str = "AEO__") -> dict[str, object]:
    """Every environment variable this settings tree accepts, with its default.

    Mirrors pydantic-settings' own nested-delimiter scheme (env_prefix="AEO__",
    env_nested_delimiter="__"), so the names here are the names an operator types.
    """
    out: dict[str, object] = {}
    for name, field in model.model_fields.items():
        ann = field.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out.update(_env_names(ann, f"{prefix}{name.upper()}__"))
        else:
            out[f"{prefix}{name.upper()}"] = field.default
    return out


def _mentioned() -> set[str]:
    return set(re.findall(r"AEO__[A-Z0-9_]+", DEPLOY_MD.read_text(encoding="utf-8")))


def test_every_payments_variable_is_documented():
    """The money path, in full. Four of these were missing when this test was written:
    ENABLED, SUCCESS_PATH, CANCEL_PATH and REQUEST_TIMEOUT_SEC."""
    known = _env_names(Settings)
    payments = {v for v in known if v.startswith("AEO__PAYMENTS__")}
    assert payments, "the payments settings group vanished — this test is now checking nothing"
    missing = sorted(payments - _mentioned())
    assert not missing, (
        "DEPLOY.md does not document these payments variables: "
        + ", ".join(missing)
        + ". Add them to the Payments table in the Environment reference."
    )


def test_deploy_md_mentions_no_variable_that_does_not_exist():
    """A documented variable the code never reads is followed, does nothing, and reads as a
    broken product. DEPLOY.md shipped exactly one of those until Phase 4.2."""
    known = set(_env_names(Settings))
    ghosts = sorted(_mentioned() - known)
    assert not ghosts, (
        "DEPLOY.md documents variables that do not exist in src/aeo/settings.py: "
        + ", ".join(ghosts)
    )


def test_the_fatal_boot_pairs_are_called_out_by_name():
    """The two half-configurations that are fatal by design must be findable in the doc — the
    whole reason they are fatal is that they are silent otherwise."""
    doc = DEPLOY_MD.read_text(encoding="utf-8")
    for var in ("AEO__PAYMENTS__STRIPE_SECRET_KEY", "AEO__PAYMENTS__WEBHOOK_SECRET",
                "AEO__PAYMENTS__PUBLIC_APP_URL", "AEO__API__AUTH_KEY"):
        assert var in doc, f"{var} must be documented — it can refuse to boot"
