"""Comprehensive unit and integration test suite for the Content Engine.

Tests idea generation, script writing, fact checking, and originality verification.
"""

import pytest
from src.content.idea_generator import ContentIdea, ContentIdeaGenerator, EVERGREEN_TEMPLATES
from src.content.script_writer import ScriptWriter
from src.content.fact_checker import FactCheckResult, FactChecker
from src.content.originality_checker import OriginalityResult, OriginalityChecker


class TestIdeaGenerator:
    """Test suite for ContentIdeaGenerator."""

    def test_evergreen_categories_and_counts(self):
        """Verify all 8 niches exist and contain at least 50 templates each."""
        generator = ContentIdeaGenerator()
        expected_niches = ["science", "technology", "education", "history", "nature", "psychology", "health", "space"]
        for niche in expected_niches:
            assert niche in EVERGREEN_TEMPLATES, f"Niche '{niche}' missing from templates"
            count = len(EVERGREEN_TEMPLATES[niche])
            assert count >= 50, f"Niche '{niche}' has only {count} templates (expected >= 50)"

    def test_generate_ideas_count_and_fields(self):
        """Verify generation returns structured ContentIdea instances with valid fields."""
        generator = ContentIdeaGenerator()
        ideas = generator.generate_ideas(count=5, niche="space")
        assert len(ideas) == 5
        for idea in ideas:
            assert isinstance(idea, ContentIdea)
            assert idea.topic
            assert idea.title
            assert idea.description
            assert idea.niche == "space"
            assert 1.0 <= idea.score <= 10.0
            d = idea.to_dict()
            assert "id" in d
            assert "topic" in d
            assert "score" in d

    def test_deduplication_and_marking_used(self):
        """Verify previously used topics are filtered out."""
        generator = ContentIdeaGenerator()
        ideas = generator.generate_ideas(count=1, niche="history")
        selected = ideas[0]

        generator.mark_topic_used(selected.topic, selected.title)

        # Generate larger batch and verify the marked topic is not repeated
        new_batch = generator.generate_ideas(count=10, niche="history")
        topics = [i.topic for i in new_batch]
        assert selected.topic not in topics

    def test_get_trending_topics(self):
        """Verify get_trending_topics returns rated trending concepts."""
        generator = ContentIdeaGenerator()
        trends = generator.get_trending_topics(count=8)
        assert len(trends) == 8
        for t in trends:
            assert "topic" in t
            assert "trend_score" in t
            assert "virality_potential" in t
            assert "recommended_format" in t

    def test_engagement_score_heuristics(self):
        """Verify headline engagement score heuristics."""
        generator = ContentIdeaGenerator()
        score1 = generator.calculate_engagement_score(
            title="Why Einstein Was Baffled by This 1 Secret Paradox",
            description="A detailed analysis of quantum theory that broke modern physics.",
            base_score=8.0,
        )
        score2 = generator.calculate_engagement_score(
            title="General physics concepts report",
            description="Short note",
            base_score=8.0,
        )
        assert score1 > score2


class TestScriptWriter:
    """Test suite for ScriptWriter."""

    def test_short_script_constraints(self):
        """Verify short scripts meet speech duration and word count constraints."""
        writer = ScriptWriter()
        for style in ["explainer", "listicle", "narrative", "story"]:
            res = writer.write_script(
                idea={"topic": "Quantum Tunneling", "title": "Particles Walking Through Walls", "description": "How atoms tunnel through barriers."},
                video_type="short",
                style=style,
            )
            assert isinstance(res, dict)
            assert 70 <= res["word_count"] <= 150
            assert 30.0 <= res["estimated_duration_seconds"] <= 60.0
            assert "title" in res
            assert "hook" in res
            assert "script_sections" in res
            assert len(res["script_sections"]) >= 2
            assert "cta" in res
            assert "full_script" in res
            assert "script" in res
            assert "description" in res
            assert "tags" in res
            assert "#Shorts" in res["description"] or "Shorts" in res["tags"]

    def test_long_script_constraints(self):
        """Verify long scripts meet 5-10 minute constraints (~750-1500 words)."""
        writer = ScriptWriter()
        res = writer.write_script(
            idea={"topic": "The Fermi Paradox", "title": "Where Are All the Aliens?", "description": "Analyzing the Great Filter and Drake Equation."},
            video_type="long",
            style="explainer",
        )
        assert isinstance(res, dict)
        assert 750 <= res["word_count"] <= 1500
        assert 300.0 <= res["estimated_duration_seconds"] <= 600.0
        assert len(res["script_sections"]) >= 4

    def test_write_script_keyword_alias_topic(self):
        """Verify write_script accepts topic keyword parameter."""
        writer = ScriptWriter()
        res = writer.write_script(topic="James Webb Discoveries", video_type="short")
        assert res is not None
        assert "James Webb Discoveries" in res["title"] or "James Webb" in res["full_script"]


class TestFactChecker:
    """Test suite for FactChecker."""

    def test_clean_science_passes(self):
        """Verify scientifically accurate text passes with high confidence."""
        checker = FactChecker()
        script = (
            "Photosynthesis is the biological process by which green plants and certain organisms "
            "convert light energy, typically from the sun, into chemical energy in the form of sugars."
        )
        result = checker.check_script(script)
        assert result.passed is True
        assert result.confidence_score >= 0.90
        assert len(result.flagged_claims) == 0

    def test_medical_red_flags_caught(self):
        """Verify medical misinformation is flagged and rejected."""
        checker = FactChecker()
        script = "This ancient tea cures cancer overnight! Stop taking your prescribed medication and use this miracle remedy."
        result = checker.check_script(script)
        assert result.passed is False
        assert any(c["category"] == "medical" for c in result.flagged_claims)
        assert len(result.red_flags) >= 2

    def test_financial_red_flags_caught(self):
        """Verify get-rich-quick and guaranteed returns are caught."""
        checker = FactChecker()
        script = "Invest in this 1000x crypto coin now for guaranteed returns. It is a completely risk-free investment."
        result = checker.check_script(script)
        assert result.passed is False
        assert any(c["category"] == "financial" for c in result.flagged_claims)

    def test_pseudoscience_and_legal_myths_caught(self):
        """Verify 10% brain myth and tax loophole claims are flagged."""
        checker = FactChecker()
        script = "Humans only use 10% of their brain. Also, here is a secret loophole to avoid paying taxes legally."
        result = checker.check_script(script)
        assert result.passed is False
        assert len(result.flagged_claims) >= 2


class TestOriginalityChecker:
    """Test suite for OriginalityChecker."""

    def test_exact_dedup_hash(self):
        """Verify exact content matches are rejected via SHA-256."""
        checker = OriginalityChecker(default_threshold=0.70)
        script = "The Apollo guidance computer was a core milestone in software engineering and digital navigation."
        checker.register_script("vid-101", "Apollo Guidance", script)

        result = checker.is_original(script)
        assert result.is_original is False
        assert result.similarity_score == 1.0
        assert result.matched_id == "vid-101"

    def test_fuzzy_dedup_threshold(self):
        """Verify fuzzy near-duplicates above 0.7 are rejected, and distinct scripts pass."""
        checker = OriginalityChecker(default_threshold=0.70)
        base = "Superconductors transmit electrical current with zero resistance when cooled below their critical temperature."
        checker.register_script("vid-102", "Superconductors", base)

        near_duplicate = "Superconductors transmit electric currents with zero resistance when cooled below critical temperature."
        res_near = checker.is_original(near_duplicate)
        assert res_near.is_original is False
        assert res_near.similarity_score >= 0.70

        distinct = "The Silk Road was an ancient network of Eurasian trade routes active from the Han dynasty."
        res_distinct = checker.is_original(distinct)
        assert res_distinct.is_original is True
        assert res_distinct.similarity_score < 0.45

    def test_find_similar_top_n(self):
        """Verify find_similar returns sorted matches."""
        checker = OriginalityChecker()
        checker.register_script("vid-201", "Black Holes", "Black holes are regions of spacetime where gravity is so strong nothing escapes.")
        checker.register_script("vid-202", "Neutron Stars", "Neutron stars are the collapsed cores of massive supergiant stars.")

        matches = checker.find_similar("Black holes are regions of space where gravitational pull is inescapable.", top_n=2)
        assert len(matches) == 2
        assert matches[0]["video_id"] == "vid-201"
        assert matches[0]["similarity"] > matches[1]["similarity"]
