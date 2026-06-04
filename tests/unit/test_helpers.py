"""Pure scoring/text helpers — no I/O, no fixtures needed."""

from __future__ import annotations

from typing import ClassVar

import pytest

from aeo.nlp import tone
from aeo.scoring.result import clamp_tier, priority_tier, tier_from_thresholds
from aeo.utils.text import sentence_split, word_count


class TestClampTier:
    @pytest.mark.parametrize(
        "raw, expected",
        [(-3, 1), (0, 1), (1, 1), (3, 3), (5, 5), (6, 5), (99, 5), (3.2, 3), (4.7, 5)],
    )
    def test_bounds_and_rounding(self, raw, expected):
        assert clamp_tier(raw) == expected

    def test_custom_bounds(self):
        assert clamp_tier(10, lo=0, hi=3) == 3
        assert clamp_tier(-1, lo=0, hi=3) == 0


class TestTierFromThresholds:
    STATS: ClassVar[dict[int, int]] = {1: 0, 2: 1, 3: 3, 4: 6, 5: 10}

    @pytest.mark.parametrize(
        "value, expected",
        [(0, 1), (0.9, 1), (1, 2), (2, 2), (3, 3), (5, 3), (6, 4), (9, 4), (10, 5), (250, 5)],
    )
    def test_highest_threshold_met(self, value, expected):
        assert tier_from_thresholds(value, self.STATS) == expected

    def test_float_thresholds(self):
        ratio_tiers = {1: 0.0, 2: 0.5, 3: 1.0, 4: 1.5, 5: 2.5}
        assert tier_from_thresholds(0.0, ratio_tiers) == 1
        assert tier_from_thresholds(0.49, ratio_tiers) == 1
        assert tier_from_thresholds(0.5, ratio_tiers) == 2
        assert tier_from_thresholds(2.5, ratio_tiers) == 5
        assert tier_from_thresholds(100.0, ratio_tiers) == 5


class TestPriorityTier:
    @pytest.mark.parametrize(
        "total, expected",
        [(0, "critical"), (10, "critical"), (13, "critical"),
         (14, "high"), (20, "high"), (21, "high"),
         (22, "medium"), (29, "medium"),
         (30, "low"), (37, "low"), (40, "low")],
    )
    def test_buckets(self, total, expected):
        # max_possible fixed at 40 → 35% / 55% / 75% boundaries.
        assert priority_tier(total, 40) == expected

    def test_zero_max_is_safe(self):
        assert priority_tier(0, 0) == "critical"


class TestTone:
    def test_promotional_copy_flagged(self):
        text = ("We deliver an industry-leading, best-in-class, cutting-edge, "
                "world-class platform that is robust and trusted by teams everywhere.")
        result = tone.analyze(text)
        assert result["is_promotional"] is True
        assert result["marketing_count"] >= 5

    def test_substantive_copy_not_flagged(self):
        text = ("Vulnerability management identifies, prioritizes, and remediates "
                "weaknesses. Teams that patch critical issues within thirty days "
                "avoid the majority of intrusions, according to the dataset.")
        result = tone.analyze(text)
        assert result["is_promotional"] is False

    def test_empty_text(self):
        result = tone.analyze("")
        assert result["word_count"] == 0
        assert result["promotional_density_per_1k"] == 0.0


class TestTextUtils:
    def test_word_count(self):
        assert word_count("one two three") == 3
        assert word_count("") == 0

    def test_sentence_split(self):
        sentences = sentence_split("First sentence. Second one! A third?")
        assert len(sentences) == 3
