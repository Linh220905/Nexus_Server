"""
Adaptive Teaching Engine — Generates conversation prompts for teaching.

No step machine. Each turn, LLM receives current_word, student_input,
and conversation history. LLM decides advance + wait_for_student.
Code only tracks word_index and enforces rules.
"""
from __future__ import annotations

import json
from typing import Any

from app.server_logging import get_logger
from app.services.scoring import FlexibleScoringEngine

logger = get_logger(__name__)


class AdaptiveTeachingEngine:
    """Engine to build natural conversation prompts for vocabulary teaching."""

    def __init__(self, llm_service=None):
        self._scoring = FlexibleScoringEngine()

    def build_teaching_prompt(
        self,
        *,
        student_input: str,
        current_word: str,
        word_meaning: str,
        word_examples: list[dict],
        word_objects: list[str],
        next_word: str | None = None,
        next_word_meaning: str | None = None,
        word_index: int = 0,
        total_words: int = 1,
        conversation_history: list[dict] | None = None,
        land_name: str = "",
        player_name: str = "",
    ) -> str:
        """
        Build the user_text portion of the LLM prompt.

        Framed as a conversation, not a task instruction.
        Code injects teaching context; LLM thinks it's having a natural chat.
        """
        history_text = ""
        if conversation_history:
            recent = conversation_history[-3:] if len(conversation_history) > 3 else conversation_history
            lines = []
            for msg in recent:
                role = "Teacher" if msg.get("role") == "assistant" else "Student"
                content = str(msg.get("content", "")).strip()
                if content:
                    lines.append(f"{role}: {content}")
            if lines:
                history_text = "\n".join(lines)

        is_first_word = word_index == 0
        is_last_word = word_index >= total_words - 1

        parts = []

        if not is_first_word and student_input and student_input != "__CONTINUATION__":
            parts.append(
                f"[Student just responded]\n"
                f'"{student_input}"\n'
            )

        parts.append(
            f"[Current teaching target — keep teaching until student says it correctly]\n"
            f"Word: \"{current_word}\" — meaning: {word_meaning}"
        )

        if word_examples:
            ex = word_examples[0]
            parts.append(f"Example: {ex.get('text', '')} ({ex.get('translation', '')})")

        if word_objects:
            parts.append(f"Related: {', '.join(word_objects[:3])}")

        if next_word and next_word_meaning:
            parts.append(f"[Next word to introduce after this one: \"{next_word}\" ({next_word_meaning})]")

        if not is_first_word:
            parts.append(
                f"[Progress: word {word_index + 1} of {total_words}]"
            )

        if land_name and (is_first_word or word_index == 0):
            parts.append(f"[Lesson is set in {land_name}]")

        prompt = "\n".join(parts)

        if history_text:
            prompt = f"[Recent conversation]\n{history_text}\n\n{prompt}"

        return prompt

    def evaluate_with_backstop(
        self,
        student_text: str,
        expected_answer: str | None,
        llm_advance: bool,
    ) -> dict:
        """
        Scoring backstop: use FlexibleScoringEngine to validate LLM's advance decision.

        Returns corrected decision:
        - If scoring says clearly correct (>0.7) → force advance=true
        - If scoring says clearly wrong (<0.3) → force advance=false
        - Otherwise → respect LLM's decision (grey zone)
        """
        result = {
            "advance": llm_advance,
            "confidence": 0.5,
            "method": "llm_only",
        }

        if not student_text or not expected_answer:
            return result

        score = self._scoring.score(student_text, expected_answer)

        # Hard cutoff: clearly correct (threshold 0.75 như user yêu cầu)
        if score.is_correct and score.confidence >= 0.75:
            result["advance"] = True
            result["confidence"] = score.confidence
            result["method"] = "backstop_forced_correct"
            return result

        # Hard cutoff: clearly wrong
        if score.confidence < 0.30:
            result["advance"] = False
            result["confidence"] = score.confidence
            result["method"] = "backstop_forced_wrong"
            return result

        # Grey zone: respect LLM
        result["advance"] = llm_advance
        result["confidence"] = score.confidence
        result["method"] = "llm_decided"
        return result

    def student_input_is_valid(self, raw_input: str | None) -> bool:
        """Check if student actually said something meaningful."""
        if not raw_input:
            return False
        text = raw_input.strip()
        if not text or text == "__CONTINUATION__":
            return False
        if len(text) < 2:
            return False
        # Filter pure noise/hesitation
        noise = {"um", "uh", "ah", "er", "hmm", "mm", "mhm", "ưm", "à", "ờ", "ừ"}
        if text.lower() in noise:
            return False
        return True

    def get_next_teaching_word(
        self,
        words: list[dict],
        current_index: int,
        direction: int = 1,
    ) -> dict | None:
        """Get the next word without advancing the index (LLM doesn't control index)."""
        next_idx = current_index + direction
        if 0 <= next_idx < len(words):
            return words[next_idx]
        return None

    def is_lesson_complete(self, current_index: int, total_steps: int) -> bool:
        return current_index >= total_steps - 1
