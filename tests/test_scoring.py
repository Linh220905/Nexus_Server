"""
Tests for FlexibleScoringEngine — comprehensive edge case coverage.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.scoring import FlexibleScoringEngine
import pytest


# ─── Fixture ───────────────────────────────────────────────────────

@pytest.fixture
def engine() -> FlexibleScoringEngine:
    return FlexibleScoringEngine(threshold=0.55)


# ─── 1. Exact match cases ─────────────────────────────────────────

class TestExactMatch:

    def test_exact_word(self, engine):
        r = engine.score("red", "red")
        assert r.is_correct
        assert r.confidence == 1.0
        assert r.method == "exact_token"

    def test_exact_word_case_insensitive(self, engine):
        r = engine.score("Red", "red")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_one_of_many_tokens(self, engine):
        r = engine.score("the fire such as red", "red")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_repeated_word_once(self, engine):
        r = engine.score("red red red", "red")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_repeated_word_with_pause(self, engine):
        r = engine.score("red, red, red", "red")
        assert r.is_correct
        assert r.confidence == 1.0


# ─── 2. Substring match cases ─────────────────────────────────────

class TestSubstringMatch:

    def test_token_contains_expected(self, engine):
        r = engine.score("reddish", "red")
        assert r.is_correct
        assert r.confidence == 0.95

    def test_expected_in_phrase(self, engine):
        r = engine.score("the fire such as red", "red")
        assert r.is_correct


# ─── 3. Near-pronunciation cases (phát âm gần) ───────────────────

class TestNearPronunciation:

    def test_rat_for_red(self, engine):
        r = engine.score("rat", "red")
        assert r.is_correct, f"rat→red failed, score={r.confidence}"
        assert r.confidence >= 0.55

    def test_right_for_red(self, engine):
        r = engine.score("right", "red")
        assert r.is_correct, f"right→red failed, score={r.confidence}"
        assert r.confidence >= 0.55

    def test_rad_for_red(self, engine):
        r = engine.score("rad", "red")
        assert r.is_correct
        assert r.confidence >= 0.55

    def test_rid_for_red(self, engine):
        r = engine.score("rid", "red")
        assert r.is_correct
        assert r.confidence >= 0.55

    def test_read_for_red(self, engine):
        r = engine.score("read", "red")
        assert r.is_correct
        assert r.confidence >= 0.7

    def test_bled_for_blue(self, engine):
        r = engine.score("bled", "blue")
        assert r.is_correct, f"bled→blue failed, score={r.confidence}"
        assert r.confidence >= 0.55

    def test_bloo_for_blue(self, engine):
        r = engine.score("bloo", "blue")
        assert r.is_correct
        assert r.confidence >= 0.55

    def test_yellow_mispelled(self, engine):
        r = engine.score("yello", "yellow")
        assert r.is_correct, f"yello→yellow failed, score={r.confidence}"
        assert r.confidence >= 0.5


# ─── 4. Definitely wrong cases ────────────────────────────────────

class TestWrongAnswers:

    def test_green_for_red(self, engine):
        r = engine.score("green", "red")
        assert not r.is_correct, f"green→red shouldn't match, score={r.confidence}"
        assert r.confidence < 0.55

    def test_blue_for_red(self, engine):
        r = engine.score("blue", "red")
        assert not r.is_correct
        assert r.confidence < 0.55

    def test_yellow_for_red(self, engine):
        r = engine.score("yellow", "red")
        assert not r.is_correct

    def test_apple_for_red(self, engine):
        r = engine.score("apple", "red")
        assert not r.is_correct

    def test_completely_different(self, engine):
        r = engine.score("i like dogs and cats", "red")
        assert not r.is_correct


# ─── 5. Token-in-sentence cases ───────────────────────────────────

class TestTokenInSentence:

    def test_sentence_with_blue(self, engine):
        r = engine.score("the blue sky", "blue")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_sentence_with_yellow(self, engine):
        r = engine.score("banana is yellow", "yellow")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_stt_noise_prefix(self, engine):
        r = engine.score("con tra loi la red", "red")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_stt_noise_suffix(self, engine):
        r = engine.score("red a a", "red")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_i_see_sentence(self, engine):
        r = engine.score("I see red", "red")
        assert r.is_correct
        assert r.confidence == 1.0


# ─── 6. Edge cases ────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_text(self, engine):
        r = engine.score("", "red")
        assert not r.is_correct
        assert r.confidence == 0.0

    def test_empty_expected(self, engine):
        r = engine.score("red", "")
        assert not r.is_correct
        assert r.confidence == 0.0

    def test_both_empty(self, engine):
        r = engine.score("", "")
        assert not r.is_correct
        assert r.confidence == 0.0

    def test_vietnamese_accent(self, engine):
        r = engine.score("màu đỏ là red", "red")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_vietnamese_only(self, engine):
        r = engine.score("con muon hoc tiep", "red")
        assert not r.is_correct

    def test_numbers_in_text(self, engine):
        r = engine.score("blue 2", "blue")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_punctuation_in_text(self, engine):
        r = engine.score("red!", "red")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_short_word_exact(self, engine):
        r = engine.score("in", "in")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_repeated_word_thrice(self, engine):
        r = engine.score("blue blue blue", "blue")
        assert r.is_correct
        assert r.confidence == 1.0


# ─── 7. Performance / no LLM use ──────────────────────────────────

class TestPerformance:

    def test_exact_match_method(self, engine):
        r = engine.score("red", "red")
        assert r.method == "exact_token"

    def test_wrong_method(self, engine):
        r = engine.score("green", "red")
        assert r.method in ("composite", "blended")

    def test_token_match_method(self, engine):
        r = engine.score("the red flower", "red")
        assert r.method == "exact_token"


# ─── 8. User-specified test cases ─────────────────────────────────

class TestUserCases:

    def test_the_fire_such_as_red(self, engine):
        r = engine.score("the fire such as red", "red")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_red_repeated(self, engine):
        r = engine.score("red red red", "red")
        assert r.is_correct
        assert r.confidence == 1.0

    def test_rat_approximate(self, engine):
        r = engine.score("rat", "red")
        assert r.is_correct and r.confidence >= 0.55

    def test_right_approximate(self, engine):
        r = engine.score("right", "red")
        assert r.is_correct and r.confidence >= 0.55


# ─── 9. Children real mispronunciations ───────────────────────────

class TestChildrenMispronunciation:

    def test_blue_variations(self, engine):
        assert engine.score("blu", "blue").is_correct
        assert engine.score("bluw", "blue").is_correct

    def test_green_variations(self, engine):
        assert engine.score("gren", "green").is_correct
        assert engine.score("grean", "green").is_correct

    def test_yellow_variations(self, engine):
        assert engine.score("yelo", "yellow").is_correct

    def test_orange_variations(self, engine):
        assert engine.score("orenge", "orange").is_correct

    def test_purple_variations(self, engine):
        assert engine.score("purpal", "purple").is_correct

    def test_pink_variations(self, engine):
        assert engine.score("penk", "pink").is_correct

    def test_white_variations(self, engine):
        assert engine.score("wite", "white").is_correct
        assert engine.score("whit", "white").is_correct

    def test_black_variations(self, engine):
        assert engine.score("blak", "black").is_correct

    def test_brown_variations(self, engine):
        assert engine.score("bron", "brown").is_correct

    def test_gray_variations(self, engine):
        assert engine.score("grey", "gray").is_correct

    def test_up_variations(self, engine):
        assert engine.score("ap", "up").is_correct

    def test_down_variations(self, engine):
        r = engine.score("dawn", "down")
        assert r.is_correct, f"dawn→down failed, score={r.confidence}"

    def test_rapp_for_red(self, engine):
        """'Rapp' → double p, capital R. Real STT case."""
        r = engine.score("Rapp", "red")
        assert r.is_correct, f"Rapp→red failed, score={r.confidence}"
        assert r.confidence >= 0.55

    def test_rapp_lowercase(self, engine):
        """'rapp' lowercase variant."""
        r = engine.score("rapp", "red")
        assert r.is_correct, f"rapp→red failed, score={r.confidence}"
        assert r.confidence >= 0.55


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
