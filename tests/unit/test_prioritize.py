"""
Page Prioritization tests — pure classify + rank, plus the real config.

Injected configs keep classification/ranking hand-computable; one test loads the
shipped config/prioritization.yaml to guard against drift. No DB, no network.
"""

from __future__ import annotations

from aeo.crawl.prioritize import (
    PageInput,
    PrioritizationCfg,
    classify,
    load_prioritization_cfg,
    persist_ranking,
    prioritize,
    rank,
)

CFG = PrioritizationCfg(
    base_weights={
        "pillar": 1.0,
        "product": 0.9,
        "blog": 0.8,
        "homepage": 0.7,
        "utility": 0.2,
        "default": 0.5,
    },
    url_patterns={
        "utility": ["/login", "/privacy"],
        "blog": ["/blog"],
        "pillar": ["/resource", "/guide"],
        "product": ["/product"],
    },
    precedence=("utility", "blog", "pillar", "product"),
    homepage_paths=["/home", "/index.html"],
    top_n=2,
    min_traffic_signal=1.0,
    default_type="default",
)


class TestClassify:
    def test_root_is_homepage(self):
        assert classify("https://x.com/", CFG) == "homepage"
        assert classify("https://x.com", CFG) == "homepage"  # bare domain → "/"

    def test_configured_homepage_paths(self):
        assert classify("https://x.com/home", CFG) == "homepage"
        assert classify("https://x.com/index.html", CFG) == "homepage"

    def test_section_markers(self):
        assert classify("https://x.com/blog/cve-2024", CFG) == "blog"
        assert classify("https://x.com/resources/guide-x", CFG) == "pillar"
        assert classify("https://x.com/login", CFG) == "utility"

    def test_plural_paths_match_singular_needle(self):
        assert classify("https://x.com/products/scanner", CFG) == "product"

    def test_precedence_first_match_wins(self):
        # path contains both "/product" and "/blog"; blog precedes product
        assert classify("https://x.com/product/blog-post", CFG) == "blog"

    def test_unknown_path_is_default(self):
        assert classify("https://x.com/something/else", CFG) == "default"

    def test_query_and_fragment_ignored(self):
        assert classify("https://x.com/blog/x?utm=1#top", CFG) == "blog"


class TestRank:
    def test_score_is_base_weight_times_traffic(self):
        scored = rank([PageInput("https://x.com/resources/x", internal_links=12)], CFG)
        assert scored[0].final_score == 12.0  # 1.0 * 12
        assert scored[0].page_type == "pillar"

    def test_orders_desc_and_selects_top_n(self):
        pages = [
            PageInput("https://x.com/", internal_links=20),            # 0.7*20 = 14
            PageInput("https://x.com/resources/x", internal_links=12),  # 1.0*12 = 12
            PageInput("https://x.com/products/y", internal_links=10),   # 0.9*10 = 9
            PageInput("https://x.com/blog/z", internal_links=8),        # 0.8*8  = 6.4
        ]
        scored = rank(pages, CFG)

        assert [s.page_type for s in scored] == ["homepage", "pillar", "product", "blog"]
        assert [s.rank for s in scored] == [1, 2, 3, 4]
        assert [s.selected for s in scored] == [True, True, False, False]  # top_n = 2

    def test_zero_links_floored_to_min_signal(self):
        scored = rank([PageInput("https://x.com/", internal_links=0)], CFG)
        assert scored[0].traffic_signal == 1.0
        assert scored[0].final_score == 0.7  # base_weight * 1.0

    def test_ties_break_by_weight_then_url(self):
        # both 'default' (0.5) with 10 links → score 5.0; alphabetical URL wins
        pages = [
            PageInput("https://x.com/zzz", internal_links=10),
            PageInput("https://x.com/aaa", internal_links=10),
        ]
        scored = rank(pages, CFG)
        assert [s.url for s in scored] == ["https://x.com/aaa", "https://x.com/zzz"]

    def test_prioritize_uses_loaded_cfg_when_none(self):
        scored = prioritize([PageInput("https://x.com/blog/a", internal_links=3)])
        assert scored[0].page_type == "blog"


class TestPersistRanking:
    def test_upserts_one_row_per_scored_url(self, monkeypatch):
        calls = []

        def fake_upsert(run_id, url, page_type, base_weight, traffic_signal,
                        final_score, *, final_rank=None, selected=False, detail=None):
            calls.append({"run_id": run_id, "url": url, "rank": final_rank, "selected": selected})
            return len(calls)

        from aeo.storage.repos import priorities as priorities_repo

        monkeypatch.setattr(priorities_repo, "upsert", fake_upsert)

        scored = rank(
            [
                PageInput("https://x.com/", internal_links=20),
                PageInput("https://x.com/login", internal_links=2),
            ],
            CFG,
        )
        persist_ranking(7, scored)

        assert len(calls) == 2
        assert calls[0]["run_id"] == 7
        assert calls[0]["rank"] == 1 and calls[0]["selected"] is True


class TestShippedConfig:
    def test_loads_with_expected_shape(self):
        cfg = load_prioritization_cfg()
        assert cfg.top_n == 30
        assert cfg.base_weight_for("pillar") == 1.0
        assert cfg.base_weight_for("utility") == 0.2
        # unknown type falls back to the default weight
        assert cfg.base_weight_for("does-not-exist") == cfg.base_weights["default"]

    def test_classifies_representative_cybersecurity_urls(self):
        cfg = load_prioritization_cfg()
        assert classify("https://securin.io/", cfg) == "homepage"
        assert classify("https://securin.io/products/vmdr", cfg) == "product"
        assert classify("https://securin.io/solutions/attack-surface", cfg) == "solution"
        assert classify("https://securin.io/blog/cve-2024-1234", cfg) == "blog"
        assert classify("https://securin.io/resources/what-is-cpsm", cfg) == "pillar"
        assert classify("https://securin.io/login", cfg) == "utility"
        assert classify("https://securin.io/about-us", cfg) == "about"
