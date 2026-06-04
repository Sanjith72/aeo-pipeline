"""Reference Layer — provisional best-practice targets, architecture, intent.

All offline: the loader reads config/best_practices.yaml and exposes typed
accessors. Gap analysis (60% layer) and the Recommender depend only on these
accessors, so a richer future version changes only the loader.
"""

from __future__ import annotations

from aeo.reference import classify_intent, load_reference
from aeo.reference.query_intent import QueryIntentCfg

# The 10-criterion rubric (8 shipped + 2 added in build step C).
ALL_CRITERIA = {
    "schema_markup", "qa_blocks", "stats_in_html", "entity_consistency",
    "heading_structure", "content_depth", "citation_signals", "load_speed",
    "render_accessibility", "answer_readability",
}


class TestTargets:
    def test_targets_cover_all_ten_criteria_in_range(self):
        ref = load_reference()
        assert set(ref.targets) == ALL_CRITERIA
        for crit, target in ref.targets.items():
            assert 1 <= target <= 5, f"{crit} target {target} out of 1-5"

    def test_target_for_known_and_unknown(self):
        ref = load_reference()
        assert ref.target_for("schema_markup") == ref.targets["schema_markup"]
        # Unknown criterion falls back to the mid-scale default, never raises.
        assert ref.target_for("does_not_exist") == 3


class TestArchitecture:
    def test_homepage_has_structure(self):
        ref = load_reference()
        arch = ref.architecture_for("homepage")
        assert arch.must_have, "homepage should list must-have elements"
        assert isinstance(arch.target_word_count, int)

    def test_unknown_page_type_falls_back_to_default(self):
        ref = load_reference()
        assert ref.architecture_for("no_such_type") is ref.architecture["default"]


class TestQueryIntent:
    def test_url_patterns(self):
        ref = load_reference()
        assert ref.classify_intent("https://x.io/pricing") == "commercial"
        assert ref.classify_intent("https://x.io/account/login") == "navigational"
        assert ref.classify_intent("https://x.io/blog/what-is-cvss") == "informational"

    def test_default_when_no_signal(self):
        ref = load_reference()
        assert ref.classify_intent("https://x.io/x9z8q7") == ref.intent.default

    def test_heading_keywords_when_url_silent(self):
        ref = load_reference()
        intent = ref.classify_intent("https://x.io/x9z8q7", headings=["Request a demo"])
        assert intent == "commercial"


class TestIntentPrecedencePure:
    """classify_intent is a pure function over an explicit cfg — precedence is
    commercial > navigational > informational so the most business-valuable
    signal wins when a URL matches several."""

    def test_commercial_beats_informational(self):
        cfg = QueryIntentCfg(
            default="informational",
            url_patterns={
                "commercial": ["/demo"],
                "informational": ["/blog"],
            },
        )
        # URL matches both /blog and /demo → commercial wins.
        assert classify_intent("https://x.io/blog/demo", None, cfg) == "commercial"

    def test_falls_back_to_default(self):
        cfg = QueryIntentCfg(default="informational", url_patterns={"commercial": ["/buy"]})
        assert classify_intent("https://x.io/learn", None, cfg) == "informational"
