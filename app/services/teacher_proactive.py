"""
Proactive Teacher — chủ động dạy học, kể chuyện, chạy game.

Không đợi học sinh nói — robot tự mở bài, tự hỏi, tự chuyển cảnh.
"""
from __future__ import annotations

import json
import random
from typing import Any, Callable, Awaitable

from app.server_logging import get_logger
from app.prompt_store import TEACHING_SYSTEM_PROMPT, GAME_MASTER_PROMPT
from app.services.llm import LLMService
from app.services.tts import TTSService
from app.services.story_engine import (
    get_land_by_module_index,
    get_land_by_id,
    get_land_intro,
    get_game_intro,
    STORY_INTRO,
    LAND_RECORDS,
)
from app.services.game_profile import GameProfile, BADGE_DEFINITIONS
from app.services.learning_content import (
    get_topic_by_id,
    build_vocab_lesson_steps,
    build_conversation_lesson,
    get_next_lesson_from_roadmap,
)
from app.services.adaptive_teaching import AdaptiveTeachingEngine
from app.services.teaching_content import get_teaching_content_service

logger = get_logger(__name__)


class ProactiveTeacher:
    """
    Điều phối giáo viên chủ động.

    - Quản lý lesson progress (hiện tại đang ở land/lesson/step nào)
    - Sinh prompt dạy học có kể chuyện
    - Đánh giá câu trả lời, thưởng XP, badge
    - Chạy mini-game
    """

    def __init__(self, llm: LLMService, tts: TTSService | None = None):
        self._llm = llm
        self._tts = tts
        self._content_service = get_teaching_content_service()
        self._adaptive = AdaptiveTeachingEngine(llm)

    # ─── Proactive lesson flow ──────────────────────────────────

    def get_lesson_opener(
        self,
        learning_context: dict[str, str | None],
        game_profile: GameProfile | None = None,
    ) -> str:
        """
        Sinh câu nói mở đầu khi session bắt đầu.
        Dựa trên state: intro→onboarding, active→continue, complete→next.
        """
        state = str(learning_context.get("state") or "IDLE")

        if learning_context.get("intro_done") != "1":
            learning_context["intro_done"] = "1"
            learning_context["onboarding_state"] = "asked_name"
            return (
                "Xin chào! Mình là Nexus, robot từ Hành tinh Ngôn ngữ. "
                "Mình đang đi tìm các mảnh ghép từ vựng tiếng Anh bị thất lạc. "
                "Vùng đất đầu tiên là Greeting Grove, nơi mọi người đã quên cách chào hỏi nhau. "
                "Trước khi bắt đầu thám hiểm, con tên là gì nhỉ?"
            )

        if not self._ctx_str(learning_context, "player_name"):
            learning_context["onboarding_state"] = "asked_name"
            return "Con cho robot biết tên của con nhé, rồi mình bắt đầu bài học đầu tiên."

        player_name = self._ctx_str(learning_context, "player_name")

        if state == "COMPLETE":
            # Lesson vừa xong → đề xuất bài kế dùng current_lesson_id
            current_lesson_id = self._ctx_int(learning_context, "current_lesson_id", 0)
            next_lesson = get_next_lesson_from_roadmap(current_lesson_id + 1)
            if next_lesson:
                # Tìm land cho next lesson
                from app.services.learning_content import get_a1_learning_roadmap
                roadmap = get_a1_learning_roadmap()
                modules = roadmap.get("modules") or roadmap.get("units") or []
                flat_idx = -1
                next_module_idx = 0
                for mi, mod in enumerate(modules):
                    for li in range(len(mod.get("lessons", []))):
                        flat_idx += 1
                        if flat_idx == current_lesson_id + 1:
                            next_module_idx = mi
                            break
                    if flat_idx == current_lesson_id + 1:
                        break
                land = get_land_by_module_index(next_module_idx)
                intro = get_land_intro(land["id"]) if land else ""
                return (
                    f"🎉 Bài học trước {player_name} làm rất tốt! "
                    f"{intro} "
                    "Con đã sẵn sàng chưa?"
                )
            else:
                from app.services.story_engine import STORY_OUTRO
                return STORY_OUTRO

        # Đang ACTIVE → tiếp tục bài học
        current_lesson_id = self._ctx_int(learning_context, "current_lesson_id", 0)
        from app.services.learning_content import get_a1_learning_roadmap
        roadmap = get_a1_learning_roadmap()
        modules = roadmap.get("modules") or roadmap.get("units") or []
        flat_idx = -1
        module_idx = 0
        for mi, mod in enumerate(modules):
            for li in range(len(mod.get("lessons", []))):
                flat_idx += 1
                if flat_idx == current_lesson_id:
                    module_idx = mi
                    break
            if flat_idx == current_lesson_id:
                break
        land = get_land_by_module_index(module_idx)
        land_name = land["name"] if land else ""
        return (
            f"Chào mừng trở lại {player_name}! Chúng ta đang ở {land_name}. "
            "Hãy tiếp tục bài học nhé!"
        )
        land_name = land["name"] if land else ""
        return (
            f"Chào mừng trở lại {player_name}! Chúng ta đang ở {land_name}. "
            "Hãy tiếp tục bài học nhé!"
        )

    async def continue_teaching(
        self,
        learning_context: dict[str, str | None],
        game_profile: GameProfile | None = None,
        *,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
    ) -> str | None:
        """
        Sau 1 pipeline turn ở teaching mode, nếu lesson hoàn thành → đề xuất bài kế.
        Nếu lesson còn đang dạy — pipeline nội bộ tự xử lý continuation rồi,
        method này không làm gì thêm (tránh overlap).
        """
        state = str(learning_context.get("state") or "IDLE")

        if state == "COMPLETE" or learning_context.get("finished") == "1":
            opener = self.get_lesson_opener(learning_context, game_profile)
            if opener:
                await self._speak_text(
                    opener,
                    on_tts_sentence=on_tts_sentence,
                    on_tts_audio=on_tts_audio,
                    is_aborted=is_aborted,
                )
                learning_context["finished"] = "0"
                learning_context["state"] = "ACTIVE"
            return opener

        # Lesson còn ACTIVE — pipeline đã tự xử lý continuation
        return None

    # ─── Story & Game helpers ────────────────────────────────────

    async def tell_story_segment(
        self,
        land_id: str,
        *,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
    ) -> str:
        """Kể 1 đoạn cốt truyện giới thiệu vùng đất."""
        land = get_land_by_id(land_id)
        if not land:
            return ""

        text = land.get("intro", "")
        if isinstance(text, str):
            await self._speak_text(
                text,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
            )
        return text

    async def run_mini_game(
        self,
        game_type: str,
        topic: dict[str, Any] | None,
        game_state: dict[str, Any],
        student_text: str | None,
        *,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
    ) -> str | None:
        """
        Chạy mini-game.

        Args:
            game_type: "i_spy" | "simon_says" | "mystery_box" | "roleplay"
            topic: topic dict của bài học hiện tại
            game_state: {"state": "intro"|"playing"|"evaluate"|"complete", ...}
            student_text: text học sinh vừa nói (None nếu lần đầu)

        Returns: phản hồi game, None nếu lỗi.
        """
        state = game_state.get("state", "intro")

        game_context = {
            "game_type": game_type,
            "game_state": state,
            "topic": topic or {},
            "student_text": student_text or "",
        }

        try:
            llm_response = await self._llm.chat_json(
                user_text=json.dumps(game_context, ensure_ascii=False),
                system_prompt=GAME_MASTER_PROMPT,
                max_tokens=200,
                temperature=0.7,
            )
        except Exception as e:
            logger.error(f"Mini-game LLM failed: {e}")
            return None

        if llm_response:
            reply_text = str(llm_response.get("text", "")).strip() if isinstance(llm_response, dict) else str(llm_response).strip()
        else:
            reply_text = "Hãy chơi một trò chơi nhé!"

        if reply_text:
            await self._speak_text(
                reply_text,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
            )

        # Update game state
        if state == "intro":
            game_state["state"] = "playing"
        return reply_text

    # ─── Evaluation with XP ─────────────────────────────────────

    async def evaluate_and_reward(
        self,
        student_text: str,
        expected_answer: str | None,
        step_type: str,
        game_profile: GameProfile,
        is_first_try: bool = False,
    ) -> dict[str, Any]:
        """
        Đánh giá câu trả lời của học sinh, thưởng XP, trả về kết quả.

        Returns: {
            "is_correct": bool,
            "feedback": str,
            "messages": list[str],  # XP notifications
            "profile": GameProfile,
        }
        """
        evaluation = await self._adaptive.evaluate_student_response(
            student_text=student_text,
            expected_answer=expected_answer,
            step_type=step_type,
        )

        is_correct = bool(evaluation.get("is_correct", False))
        messages = []

        if is_correct:
            msgs = game_profile.add_correct_answer(is_first_try=is_first_try)
            messages.extend(msgs)
        else:
            game_profile.add_wrong_answer()
            messages.append("Không sao đâu, thử lại nhé!")

        return {
            "is_correct": is_correct,
            "feedback": str(evaluation.get("feedback", "")),
            "messages": messages,
            "profile": game_profile,
        }

    async def complete_lesson_and_reward(
        self,
        lesson_id: str,
        game_profile: GameProfile,
        learning_context: dict[str, str | None],
    ) -> list[str]:
        """Kết thúc lesson, thưởng XP, check badge. Trả về messages."""
        messages = game_profile.complete_lesson(lesson_id)

        # Kiểm tra xem đã hoàn thành toàn bộ module chưa
        module_idx = self._ctx_int(learning_context, "module_index")
        land = get_land_by_module_index(module_idx)
        if land:
            content_ids = land.get("teaching_content_ids", [])
            completed = game_profile.completed_lessons
            all_done = all(cid in completed for cid in content_ids)
            if all_done and land["id"] not in game_profile.completed_quests:
                quest_msgs = game_profile.complete_quest(land["id"])
                messages.extend(quest_msgs)

        learning_context["finished"] = "1"
        learning_context["lesson_complete"] = "1"
        return messages

    # ─── Internal helpers ───────────────────────────────────────

    async def _speak_text(
        self,
        text: str,
        *,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
        language_hint: str | None = None,
    ) -> None:
        """Gửi text qua TTS."""
        from app.services.pipeline import ConversationPipeline
        if self._tts is None:
            logger.warning("ProactiveTeacher cannot speak because TTS service is missing")
            return
        pipeline = ConversationPipeline.__new__(ConversationPipeline)
        pipeline._tts = self._tts  # type: ignore[attr-defined]
        await pipeline._speak_text(  # type: ignore[misc]
            text,
            on_tts_sentence=on_tts_sentence,
            on_tts_audio=on_tts_audio,
            is_aborted=is_aborted,
            language_hint=language_hint,
        )

    @staticmethod
    def _ctx_int(ctx: dict[str, str | None], key: str, default: int = 0) -> int:
        raw = ctx.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _ctx_str(ctx: dict[str, str | None], key: str, default: str = "") -> str:
        raw = ctx.get(key)
        return str(raw).strip() if raw is not None else default
