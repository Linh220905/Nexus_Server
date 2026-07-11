"""
FlexibleScoringEngine — Bộ chấm điểm linh hoạt cho teach mode.

Dùng kết hợp thuật toán text + phoneme (ngữ âm) để đánh giá câu trả lời
của trẻ em — khoan dung với lỗi phát âm/STT nhưng khắt khe với sai từ vựng.

Pipeline:
1. Token exact match            → score 1.0
2. Substring match              → score 0.95
3. Text composite (từ strategy 3-5 cũ)
4. Phoneme composite (mới)      → so sánh bằng âm vị
5. Blend: max(text_score, phoneme_score * 0.85)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from app.server_logging import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  PRONUNCIATION DICTIONARY  (CMUdict subset)
#  ═══════════════════════════════════════════════════════════════════
# Format: word → space-separated phonemes
# Ví dụ: "two" → "T UW"

PRONUNCIATION_DICT: dict[str, str] = {
    # ── Numbers ──
    "one": "W AH N",
    "two": "T UW",
    "three": "TH R IY",
    "four": "F AO R",
    "five": "F AY V",
    "six": "S IH K S",
    "seven": "S EH V AH N",
    "eight": "EY T",
    "nine": "N AY N",
    "ten": "T EH N",
    "zero": "Z IH R OW",
    # ── Colors ──
    "red": "R EH D",
    "blue": "B L UW",
    "green": "G R IY N",
    "yellow": "Y EH L OW",
    "orange": "AO R IH N JH",
    "purple": "P ER P AH L",
    "pink": "P IH NG K",
    "white": "W AY T",
    "black": "B L AE K",
    "brown": "B R AW N",
    "gray": "G R EY",
    "grey": "G R EY",
    "gold": "G OW L D",
    "silver": "S IH L V ER",
    # ── Verbs ──
    "run": "R AH N",
    "jump": "JH AH M P",
    "walk": "W AO K",
    "eat": "IY T",
    "drink": "D R IH NG K",
    "sleep": "S L IY P",
    "read": "R IY D",
    "write": "R AY T",
    "sing": "S IH NG",
    "dance": "D AE N S",
    "play": "P L EY",
    "swim": "S W IH M",
    "fly": "F L AY",
    "see": "S IY",
    "look": "L UH K",
    "hear": "HH IH R",
    "say": "S EY",
    "talk": "T AO K",
    "go": "G OW",
    "come": "K AH M",
    "sit": "S IH T",
    "stand": "S T AE N D",
    "stop": "S T AA P",
    "like": "L AY K",
    "love": "L AH V",
    "want": "W AA N T",
    "have": "HH AE V",
    "make": "M EY K",
    # ── Animals ──
    "cat": "K AE T",
    "dog": "D AO G",
    "bird": "B ER D",
    "fish": "F IH SH",
    "monkey": "M AH NG K IY",
    "lion": "L AY AH N",
    "elephant": "EH L AH F AH N T",
    "tiger": "T AY G ER",
    "bear": "B EH R",
    "rabbit": "R AE B IH T",
    "pig": "P IH G",
    "cow": "K AW",
    "horse": "HH AO R S",
    "duck": "D AH K",
    "chicken": "CH IH K AH N",
    "frog": "F R AO G",
    "snake": "S N EY K",
    "mouse": "M AW S",
    "turtle": "T ER T AH L",
    # ── Food ──
    "apple": "AE P AH L",
    "banana": "B AH N AE N AH",
    "bread": "B R EH D",
    "cake": "K EY K",
    "candy": "K AE N D IY",
    "cookie": "K UH K IY",
    "milk": "M IH L K",
    "water": "W AO T ER",
    "juice": "JH UW S",
    "rice": "R AY S",
    "meat": "M IY T",
    "egg": "EH G",
    "soup": "S UW P",
    # ── Body ──
    "head": "HH EH D",
    "hand": "HH AE N D",
    "foot": "F UH T",
    "eye": "AY",
    "ear": "IH R",
    "nose": "N OW Z",
    "mouth": "M AW TH",
    "arm": "AA R M",
    "leg": "L EH G",
    "teeth": "T IY TH",
    "hair": "HH EH R",
    "face": "F EY S",
    # ── Family ──
    "mother": "M AH DH ER",
    "father": "F AA DH ER",
    "sister": "S IH S T ER",
    "brother": "B R AH DH ER",
    "baby": "B EY B IY",
    "family": "F AE M AH L IY",
    "grandma": "G R AE N D M AA",
    "grandpa": "G R AE N D P AA",
    "parents": "P EH R AH N T S",
    # ── Greetings ──
    "hello": "HH EH L OW",
    "hi": "HH AY",
    "goodbye": "G UH D B AY",
    "thanks": "TH AE NG K S",
    "please": "P L IY Z",
    "sorry": "S AA R IY",
    "welcome": "W EH L K AH M",
    # ── Misc ──
    "book": "B UH K",
    "school": "S K UW L",
    "home": "HH OW M",
    "house": "HH AW S",
    "room": "R UW M",
    "table": "T EY B AH L",
    "chair": "CH EH R",
    "door": "D AO R",
    "window": "W IH N D OW",
    "teacher": "T IY CH ER",
    "student": "S T UW D AH N T",
    "friend": "F R EH N D",
    "toy": "T OY",
    "ball": "B AO L",
    "car": "K AA R",
    "tree": "T R IY",
    "flower": "F L AW ER",
    "star": "S T AA R",
    "moon": "M UW N",
    "sun": "S AH N",
    "rain": "R EY N",
    "snow": "S N OW",
    "up": "AH P",
    "down": "D AW N",
    "in": "IH N",
    "out": "AW T",
    "big": "B IH G",
    "small": "S M AO L",
    "hot": "HH AA T",
    "cold": "K OW L D",
    "happy": "HH AE P IY",
    "new": "N UW",
    "good": "G UH D",
    "day": "D EY",
    "night": "N AY T",
    "name": "N EY M",
    "yes": "Y EH S",
    "no": "N OW",
}

# ═══════════════════════════════════════════════════════════════════
#  G2P (Grapheme-to-Phoneme) — dùng khi từ không có trong dict
#  ═══════════════════════════════════════════════════════════════════

# Consonant digraphs (dài nhất trước để match greedy)
_CONS_DIGRAPHS: dict[str, str] = {
    "tch": "CH",
    "dge": "JH",
    "ch": "CH",
    "sh": "SH",
    "th": "TH",
    "ph": "F",
    "wh": "W",
    "ng": "NG",
    "ck": "K",
    "kn": "N",
    "wr": "R",
    "qu": "KW",
    "gh": "F",
}

_VOWEL_DIGRAPHS: dict[str, str] = {
    "ee": "IY",
    "ea": "IY",
    "oo": "UW",
    "oi": "OY",
    "oy": "OY",
    "ou": "AW",
    "ow": "OW",
    "ai": "EY",
    "ay": "EY",
    "aw": "AO",
    "au": "AO",
    "er": "ER",
    "ar": "AA",
    "or": "AO",
    "ir": "ER",
    "ur": "ER",
    "oa": "OW",
    "ue": "UW",
    "ie": "IY",
}

_VOWEL_LETTERS = frozenset("aeiou")

_SIMPLE_CONS: dict[str, str] = {
    "b": "B", "d": "D", "f": "F", "g": "G",
    "h": "HH", "j": "JH", "k": "K", "l": "L",
    "m": "M", "n": "N", "p": "P", "r": "R",
    "s": "S", "t": "T", "v": "V", "w": "W",
    "x": "K S", "y": "Y", "z": "Z",
    "c": "K", "q": "K",
}

# Single vowel → phoneme mapping (context-free approximation)
_SIMPLE_VOWEL: dict[str, str] = {
    "a": "AE", "e": "EH", "i": "IH",
    "o": "AA", "u": "UH",
}

# Double same vowel → mapping
_DOUBLE_VOWEL: dict[str, str] = {
    "aa": "AA", "ii": "IY", "uu": "UW",
    "ee": "IY", "oo": "UW",
}


def _g2p(word: str) -> str:
    """
    Grapheme-to-Phoneme guesser — ước lượng phiên âm từ mặt chữ.

    Dùng cho STT noise không có trong pronunciation dict.
    "chuu" → "CH UW",  "apen" → "AE P AH N",  "fai" → "F AY"
    """
    word = word.lower()
    phonemes: list[str] = []
    i = 0
    n = len(word)

    while i < n:
        # --- Consonant digraph (3 char) ---
        if i + 3 <= n:
            chunk = word[i:i+3]
            if chunk in _CONS_DIGRAPHS:
                phonemes.append(_CONS_DIGRAPHS[chunk])
                i += 3
                continue

        # --- Consonant digraph (2 char) ---
        if i + 2 <= n:
            chunk = word[i:i+2]
            if chunk in _CONS_DIGRAPHS:
                phonemes.append(_CONS_DIGRAPHS[chunk])
                i += 2
                continue

        ch = word[i]

        # --- Vowel ---
        if ch in _VOWEL_LETTERS:
            # Double vowel?
            if i + 2 <= n:
                vv = word[i:i+2]
                if vv in _DOUBLE_VOWEL:
                    phonemes.append(_DOUBLE_VOWEL[vv])
                    i += 2
                    continue
                if vv in _VOWEL_DIGRAPHS:
                    phonemes.append(_VOWEL_DIGRAPHS[vv])
                    i += 2
                    continue
            # Final 'e' in CVCe pattern: silent
            if ch == 'e' and i == n - 1 and len(phonemes) > 0:
                i += 1  # silent e
                continue
            # 'en' at end of word → AH N (unstressed)
            if ch == 'e' and i == n - 2 and n >= 3 and word[i+1] == 'n':
                phonemes.append("AH")
                i += 1
                continue
            # Single vowel
            phonemes.append(_SIMPLE_VOWEL.get(ch, "AH"))
            i += 1
            continue

        # --- Consonant ---
        mapped = _SIMPLE_CONS.get(ch, "")
        if mapped:
            for p in mapped.split():
                phonemes.append(p)
        i += 1

    return " ".join(phonemes)


def _word_to_phonemes(word: str) -> list[str]:
    """Tra từ điển, fallback về G2P. Trả về list phoneme."""
    key = word.lower().strip()
    result = PRONUNCIATION_DICT.get(key)
    if result:
        return result.split()
    guess = _g2p(key)
    return guess.split()


# ═══════════════════════════════════════════════════════════════════
#  PHONEME SIMILARITY GROUPS
#  ═══════════════════════════════════════════════════════════════════

# Nhóm ngữ âm — phoneme trong cùng nhóm có cost thấp
_PHON_GROUP: dict[str, int] = {
    "T": 1, "D": 1, "CH": 1, "JH": 1,  # coronal obstruents
    "P": 2, "B": 2,                    # labial stops
    "K": 3, "G": 3,                    # velar stops
    "F": 4, "V": 4,                    # labiodental fricatives
    "TH": 5, "DH": 5,                  # dental fricatives
    "S": 6, "Z": 6,                    # alveolar fricatives
    "SH": 7, "ZH": 7,                  # postalveolar fricatives
    "M": 8, "N": 8, "NG": 8,           # nasals
    "L": 9, "R": 9, "W": 9, "Y": 9,   # approximants
    "HH": 10,                          # glottal
    # Vowels (groups 20+)
    "IY": 20, "IH": 20, "EY": 20, "EH": 20, "AE": 20,  # front
    "AA": 21, "AO": 21, "OW": 21, "UH": 21, "UW": 21,  # back
    "AH": 22, "ER": 22,  # central
    "AY": 23, "OY": 23, "AW": 23,  # diphthongs
}

# Cross-group similarity: nhóm nào gần nhau về mặt cấu âm
_PHON_CROSS: dict[int, set[int]] = {
    1: {6, 7},     # coronal ↔ alveolar fric / postalveolar fric
    5: {6},        # dental fric ↔ alveolar fric (TH↔S — STT hay nhầm)
    6: {7, 1, 5},  # alveolar fric ↔ coronal / dental fric
    7: {6, 1},     # postalveolar fric ↔ alveolar fric / coronal
    8: {9},        # nasals ↔ approximants
    20: {21, 22},  # front ↔ back / central
    21: {20, 22},  # back ↔ front / central
    22: {20, 21},  # central ↔ front / back
}


def _phoneme_sub_cost(p1: str, p2: str) -> float:
    """Phoneme substitution cost (0.0=giống, 1.0=khác hẳn)."""
    if p1 == p2:
        return 0.0
    g1 = _PHON_GROUP.get(p1, 0)
    g2 = _PHON_GROUP.get(p2, 0)
    if g1 == 0 and g2 == 0:
        return 1.0
    if g1 == g2:
        return 0.35  # cùng nhóm cấu âm
    if g2 in _PHON_CROSS.get(g1, set()):
        return 0.55  # nhóm gần
    # Một là vowel, một là consonant → 0.75 (vẫn hơn hoàn toàn khác)
    if (g1 >= 20 and g2 < 20) or (g2 >= 20 and g1 < 20):
        return 0.75
    return 1.0


def _weighted_phoneme_dist(seq1: list[str], seq2: list[str]) -> float:
    """Weighted Levenshtein trên phoneme sequences. Trả về distance."""
    n, m = len(seq1), len(seq2)
    if n == 0:
        return float(m)
    if m == 0:
        return float(n)

    prev = [0.0] * (m + 1)
    curr = [0.0] * (m + 1)

    for j in range(m + 1):
        prev[j] = float(j)

    for i in range(1, n + 1):
        curr[0] = float(i)
        for j in range(1, m + 1):
            cost = _phoneme_sub_cost(seq1[i-1], seq2[j-1])
            curr[j] = min(
                prev[j] + 0.6,       # deletion (del = 0.6, nhẹ hơn sub)
                curr[j-1] + 0.6,     # insertion
                prev[j-1] + cost,    # substitution
            )
        prev, curr = curr, prev

    return prev[m]


def _phoneme_score(student_word: str, expected_word: str) -> float:
    """
    Score dựa trên phoneme.
    Trả về 0.0-1.0 (cao hơn = giống hơn về mặt phát âm).
    """
    ph_student = _word_to_phonemes(student_word)
    ph_expected = _word_to_phonemes(expected_word)

    if not ph_student or not ph_expected:
        return 0.0

    dist = _weighted_phoneme_dist(ph_student, ph_expected)
    max_len = max(len(ph_student), len(ph_expected))
    if max_len == 0:
        return 0.0

    sim = 1.0 - (dist / max_len)
    return max(0.0, min(1.0, sim))


# ═══════════════════════════════════════════════════════════════════
#  SCORE RESULT
#  ═══════════════════════════════════════════════════════════════════

@dataclass
class ScoreResult:
    """Kết quả chấm điểm cho một câu trả lời."""
    is_correct: bool
    confidence: float   # 0.0 - 1.0
    method: str         # Tên strategy được dùng
    details: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
#  FLEXIBLE SCORING ENGINE
#  ═══════════════════════════════════════════════════════════════════

class FlexibleScoringEngine:
    """
    Engine chấm điểm linh hoạt, thuần thuật toán.

    Kết hợp text similarity + phoneme similarity để đánh giá
    câu trả lời của trẻ em một cách khoan dung.

    Usage:
        engine = FlexibleScoringEngine(threshold=0.55)
        result = engine.score("chuu", "two")
        # → ScoreResult(is_correct=True, confidence=0.68, ...)
    """

    VOWELS = frozenset("aeiou")

    def __init__(self, threshold: float = 0.54,
                 phoneme_weight: float = 0.35):
        """
        Args:
            threshold: Ngưỡng kết luận is_correct (0.0-1.0).
            phoneme_weight: Trọng số pha trộn phoneme vào điểm text (0.0-1.0).
                           0.15 = phoneme chiếm 15% điểm cuối.
        """
        self._threshold = threshold
        self._phoneme_weight = phoneme_weight

    # ─── Public API ───────────────────────────────────────────────

    def score(self, student_text: str, expected_word: str) -> ScoreResult:
        """Chấm điểm câu trả lời."""
        if not student_text or not expected_word:
            return ScoreResult(False, 0.0, "empty_input")

        norm_text = self._normalize(student_text)
        norm_expected = self._normalize(expected_word)
        if not norm_text or not norm_expected:
            return ScoreResult(False, 0.0, "empty_after_normalize")

        tokens = self._tokenize(norm_text)
        if not tokens:
            return ScoreResult(False, 0.0, "no_tokens")

        # --- Strategy 1: Exact token match ---
        for token in tokens:
            if token == norm_expected:
                return ScoreResult(True, 1.0, "exact_token",
                                   {"token": token})

        # --- Strategy 2: Substring match ---
        for token in tokens:
            if norm_expected in token or token in norm_expected:
                return ScoreResult(True, 0.95, "substring",
                                   {"token": token})

        # --- Per-token composite scoring ---
        best_text_score = 0.0
        best_text_details: dict = {}
        best_token = ""
        best_phoneme_info: dict = {}
        best_text_method = "none"

        for token in tokens:
            t_text_score, method, details = self._score_text_token(
                token, norm_expected
            )
            if t_text_score > best_text_score:
                best_text_score = t_text_score
                best_text_method = method
                best_text_details = details
                best_token = token

        # --- Phoneme score cho best token ---
        ph_score = _phoneme_score(best_token, norm_expected)
        best_phoneme_info = {
            "phoneme_score": round(ph_score, 4),
            "student_phonemes": " ".join(_word_to_phonemes(best_token)),
            "expected_phonemes": " ".join(_word_to_phonemes(norm_expected)),
        }

        # --- Blend text + phoneme ---
        pw = self._phoneme_weight
        blended = best_text_score * (1.0 - pw) + ph_score * pw

        # Phoneme boost: khi text thấp mà phoneme cao → STT noise
        # (ví dụ "chuu" text=0.2, phoneme=0.825 → boost)
        if ph_score > best_text_score + 0.25 and ph_score >= 0.6:
            # Nếu lệch lớn → nghiêng về phoneme
            blend_ph = max(pw, 0.55)
            blended = best_text_score * (1.0 - blend_ph) + ph_score * blend_ph

        blended = min(max(blended, 0.0), 1.0)

        is_correct = blended >= self._threshold

        return ScoreResult(
            is_correct=is_correct,
            confidence=round(blended, 4),
            method="blended",
            details={
                "text_score": round(best_text_score, 4),
                "text_method": best_text_method,
                "phoneme_score": round(ph_score, 4),
                "blended": round(blended, 4),
                "token": best_token,
                **best_phoneme_info,
            },
        )

    # ─── Text-based token scoring (giữ nguyên từ phiên bản cũ) ─────

    def _score_text_token(self, token: str, expected: str) -> tuple[float, str, dict]:
        """Tính text-based score cho 1 token."""
        if len(token) <= 1:
            return 0.0, "too_short", {}

        p_token = self._phonetic_normalize(token)
        p_expected = self._phonetic_normalize(expected)

        max_len = max(len(p_token), len(p_expected), 1)

        # Vowel-normalized Levenshtein
        vn_token = self._vowel_norm(p_token)
        vn_exp = self._vowel_norm(p_expected)
        vn_lev = self._levenshtein(vn_token, vn_exp)
        vn_sim = 1.0 - (vn_lev / max(max_len, len(vn_token), len(vn_exp)))

        # Regular Levenshtein
        lev_dist = self._levenshtein(p_token, p_expected)
        lev_sim = 1.0 - (lev_dist / max_len)

        # First char bonus
        first_bonus = 0.15 if p_token[0] == p_expected[0] else 0.0

        # Consonant skeleton
        c_token = self._phonetic_consonant(self._consonants(p_token))
        c_exp = self._phonetic_consonant(self._consonants(p_expected))
        cons_bonus = self._consonant_bonus(c_token, c_exp)

        len_ratio = min(len(p_token), len(p_expected)) / max_len
        len_bonus = 0.05 * len_ratio

        score = max(
            vn_sim * 0.55 + first_bonus + cons_bonus,
            lev_sim * 0.35 + first_bonus * 0.5 + vn_sim * 0.15,
            vn_sim * 0.65 + first_bonus * 0.2 + len_bonus,
            first_bonus * 0.5 + vn_sim * 0.35 + cons_bonus * 0.15,
        )

        score = max(0.0, min(1.0, score))

        return score, "composite", {
            "vowel_norm_sim": round(vn_sim, 4),
            "lev_sim": round(lev_sim, 4),
            "first_bonus": round(first_bonus, 4),
            "cons_bonus": round(cons_bonus, 4),
            "len_bonus": round(len_bonus, 4),
            "text_composite": round(score, 4),
        }

    # ─── Normalization helpers ─────────────────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.lower().strip()
        nfkd = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in nfkd if not unicodedata.combining(c))
        text = re.sub(r"[^a-z\s]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [t for t in text.split() if len(t) > 1]

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        n, m = len(a), len(b)
        if n == 0:
            return m
        if m == 0:
            return n
        if n > m:
            a, b = b, a
            n, m = m, n
        prev = list(range(n + 1))
        curr = [0] * (n + 1)
        for j in range(1, m + 1):
            curr[0] = j
            for i in range(1, n + 1):
                cost = 0 if a[i - 1] == b[j - 1] else 1
                curr[i] = min(
                    prev[i] + 1,
                    curr[i - 1] + 1,
                    prev[i - 1] + cost,
                )
            prev, curr = curr, prev
        return prev[n]

    @staticmethod
    def _vowel_norm(s: str) -> str:
        return "".join("V" if c in "aeiou" else c for c in s)

    @staticmethod
    def _consonants(s: str) -> str:
        return "".join(c for c in s if c not in "aeiou")

    @staticmethod
    def _phonetic_normalize(s: str) -> str:
        s = re.sub(r'gh', '', s)
        s = re.sub(r'([bcdfghjklmnpqrstvwxyz])\1+', r'\1', s)
        return s

    @staticmethod
    def _phonetic_consonant(s: str) -> str:
        PHON_MAP = {"d": "t", "b": "p", "g": "k", "v": "f", "z": "s"}
        return "".join(PHON_MAP.get(c, c) for c in s)

    def _consonant_bonus(self, c_token: str, c_exp: str) -> float:
        if not c_token or not c_exp:
            return 0.0
        if c_token == c_exp:
            return 0.15
        if c_token.startswith(c_exp) or c_exp.startswith(c_token):
            return 0.10
        c_lev = self._levenshtein(c_token, c_exp)
        c_max = max(len(c_token), len(c_exp), 1)
        c_sim = 1.0 - (c_lev / c_max)
        return c_sim * 0.12 if c_sim >= 0.4 else 0.0

    @property
    def threshold(self) -> float:
        return self._threshold

    def set_threshold(self, value: float) -> None:
        self._threshold = max(0.0, min(1.0, value))
