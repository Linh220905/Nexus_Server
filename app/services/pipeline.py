import asyncio
import json
from app.server_logging import get_logger
import re
from typing import Callable, Awaitable

from app.mcp import MCPToolRegistry
from app.prompt_store import TEACHING_SYSTEM_PROMPT
from app.services.intent import IntentDetectorService
from app.services.stt import STTService
from app.services.llm import LLMService
from app.services.tts import TTSService
from app.services.learning_content import (
    build_conversation_lesson,
    build_vocab_lesson_steps,
    build_mode_suggestion,
    build_vocab_lesson,
    find_topic,
    get_topic_by_id,
    get_next_lesson_from_roadmap,
)
from app.services.flashcard_vocab import (
    build_finish_reply,
    build_flashcard_start_reply,
    build_next_card_prompt,
    evaluate_flashcard_answer,
    flashcard_count,
    get_flashcard_by_word,
)
from app.services.teaching_content import get_teaching_content_service
from app.services.adaptive_teaching import AdaptiveTeachingEngine
from app.services.game_profile import GameProfile
from app.database.lesson_progress import save_lesson_progress, mark_lesson_completed
from app.database.game_profile import save_game_profile

logger = get_logger(__name__)

SENTENCE_ENDINGS = frozenset(".!?;,\n")

CHUNK_MIN_CHARS = 28
CHUNK_TARGET_CHARS = 58
CHUNK_HARD_LIMIT = 90
CHUNK_PUNCT_BREAKS = frozenset(",.:")
CHUNK_SPACE_BREAK = " "


_DONE = object()

_SENTENCE_MARKER = "__sentence__"
VOCAB_BATCH_SIZE = 6
LOCK_WORD_LIMIT = 6


class ConversationPipeline:
    """
    Orchestrator: audio PCM -> text -> AI response -> audio Opus.
    Pre-fetch TTS qua asyncio.Queue để giảm giật giữa các câu.
    """

    def __init__(
        self,
        stt: STTService,
        llm: LLMService,
        tts: TTSService,
        intent_detector: IntentDetectorService | None = None,
        mcp_tools: MCPToolRegistry | None = None,
        *,
        prefer_fast_only: bool = True,
    ):
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._intent_detector = intent_detector
        self._mcp_tools = mcp_tools
        self._prefer_fast_only = prefer_fast_only

    async def process(
        self,
        pcm_data: bytes,
        chat_history: list[dict],
        *,
        interaction_mode: str | None = "free_talk",
        learning_context: dict[str, str | None] | None = None,
        on_stt_result: Callable[[str], Awaitable[None]],
        on_tts_start: Callable[[], Awaitable[None]],
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        on_tts_stop: Callable[[], Awaitable[None]],
        on_music_action: Callable[[dict], Awaitable[None]],
        on_learning_card: Callable[[dict], Awaitable[None]] | None = None,
        assignment_provider: Callable[[], Awaitable[dict | None]] | None = None,
        on_emotion: Callable[[str], Awaitable[None]] | None = None,
        is_aborted: Callable[[], bool],
    ) -> tuple[str, str] | None:
        """Chạy toàn bộ pipeline. Returns (user_text, assistant_response)."""

        # -- Bước 1: STT --
        user_text = await self._stt.transcribe(pcm_data)
        if not user_text:
            logger.info("STT returned empty, skipping")
            return None

        logger.info(f"\033[92m User: {user_text}\033[0m")
        await on_stt_result(user_text)

        # Get game profile and device_id from session if available (passed via kwargs)
        # These are set by handler.py when calling process()
        game_profile: GameProfile | None = getattr(self, '_game_profile', None)
        device_id: str | None = getattr(self, '_device_id', None)
        on_reward: Callable | None = getattr(self, '_on_reward', None)
        if game_profile is None:
            game_profile = GameProfile()

        # Track XP before teaching for reward detection
        xp_before = game_profile.total_xp
        badges_before = len(game_profile.badges)
        level_before = game_profile.level

        # -- Bước 1.5: Routing based on interaction_mode --
        if interaction_mode == "teaching":
            if learning_context is None:
                learning_context = {}
            await on_tts_start()
            reply_text = await self._handle_teaching_mode(
                user_text,
                learning_context=learning_context,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                on_emotion=on_emotion,
                is_aborted=is_aborted,
                game_profile=game_profile,
                device_id=device_id,
            )

            # ── Rewards: phát hiện XP/level/badge thay đổi ──
            if on_reward and not is_aborted():
                try:
                    if game_profile.streak > 0 and not is_aborted():
                        # Correct answer → "correct" SFX
                        await on_reward("correct", {
                            "message": f"+10 XP! Streak: {game_profile.streak}",
                            "show_image": game_profile.streak % 3 == 0,  # Show image every 3rd
                        })
                        await asyncio.sleep(1.2)  # Let SFX play before TTS

                    if game_profile.level > level_before and not is_aborted():
                        await on_reward("levelup", {
                            "message": f"Level {game_profile.level}! 🎉",
                            "show_image": True,
                        })
                        await asyncio.sleep(1.5)

                    if len(game_profile.badges) > badges_before and not is_aborted():
                        new_badge = game_profile.badges[-1]
                        from app.services.game_profile import BADGE_DEFINITIONS
                        badge_name = BADGE_DEFINITIONS.get(new_badge, {}).get("name", new_badge)
                        badge_emoji = BADGE_DEFINITIONS.get(new_badge, {}).get("emoji", "🌟")
                        await on_reward("badge", {
                            "message": f"{badge_emoji} Badge: {badge_name}!",
                            "show_image": True,
                        })
                        await asyncio.sleep(1.5)
                except Exception as e:
                    logger.warning("Reward callback error: %s", e)
            # ────────────────────────────────────────────────

            # Save progress after teaching turn
            if device_id:
                try:
                    from app.database.lesson_progress import save_lesson_progress
                    save_lesson_progress(device_id, learning_context)
                    from app.database.game_profile import save_game_profile
                    save_game_profile(device_id, game_profile)
                except Exception as e:
                    logger.warning("[%s] Failed to save teaching progress: %s", device_id, e)
            if not is_aborted():
                await on_tts_stop()
            return (user_text, reply_text)

        # Roadmap learning takes precedence if requested
        if learning_context and self._looks_like_roadmap_request(user_text):
            await on_tts_start()
            reply_text = await self._handle_roadmap_learning(
                learning_context=learning_context,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                on_learning_card=on_learning_card,
                is_aborted=is_aborted,
            )
            if not is_aborted():
                await on_tts_stop()
            return (user_text, reply_text)

        if learning_context and self._is_flashcard_vocab_active(learning_context):
            await on_tts_start()
            reply_text = await self._handle_flashcard_vocab_turn(
                user_text,
                learning_context=learning_context,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                on_learning_card=on_learning_card,
                is_aborted=is_aborted,
            )
            if not is_aborted():
                await on_tts_stop()
            return (user_text, reply_text)

        # Nếu đang ở mode học theo chủ đề (locked), ưu tiên chạy tiếp flow hiện tại, 
        # tránh nhiễu intent do STT nhiễu.
        if learning_context and self._is_learning_locked(learning_context):
            if self._looks_like_exit_learning_request(user_text):
                self._clear_learning_context(learning_context)
                await on_tts_start()
                reply_text = "Đã thoát chế độ học theo chủ đề. Bạn muốn học chủ đề nào tiếp theo?"
                await self._speak_text(
                    reply_text,
                    on_tts_sentence=on_tts_sentence,
                    on_tts_audio=on_tts_audio,
                    is_aborted=is_aborted,
                )
                if not is_aborted():
                    await on_tts_stop()
                return (user_text, reply_text)

            # Khởi tạo fallback xử lý nếu không phải câu follow-up học tập mà bị lock 
            # tránh tình trạng bẫy nhận diện nhầm.
            if not self._looks_like_learning_followup(user_text, learning_context):
                learning_context["locked"] = "0"
                learning_context["mode"] = None
                learning_context["topic_id"] = None
                learning_context["next_index"] = "0"
                learning_context["finished"] = "0"
                learning_context["lock_target_index"] = "0"
            else:
                locked_mode = str(learning_context.get("mode") or "").strip()
                locked_topic_id = str(learning_context.get("topic_id") or "").strip()

                if locked_mode == "vocabulary" and locked_topic_id:
                    selected_topic = get_topic_by_id("vocabulary", locked_topic_id)
                    if selected_topic:
                        await on_tts_start()
                        start_index = self._context_next_index(learning_context)
                        reply_text, next_index, total_words = await self._teach_vocabulary_stepwise(
                            selected_topic,
                            on_tts_sentence=on_tts_sentence,
                            on_tts_audio=on_tts_audio,
                            on_learning_card=on_learning_card,
                            is_aborted=is_aborted,
                            start_index=start_index,
                            batch_size=VOCAB_BATCH_SIZE,
                        )
                        learning_context["next_index"] = str(next_index)
                        learning_context["finished"] = "1" if next_index >= total_words else "0"
                        lock_target_index = self._lock_target_index(learning_context)
                        if next_index >= lock_target_index:
                            learning_context["locked"] = "0"
                        if not is_aborted():
                            await on_tts_stop()
                        return (user_text, reply_text)

                if locked_mode == "conversation" and locked_topic_id:
                    selected_topic = get_topic_by_id("conversation", locked_topic_id)
                    if selected_topic:
                        await on_tts_start()
                        reply_text = build_conversation_lesson(selected_topic)
                        await self._speak_text(
                            reply_text,
                            on_tts_sentence=on_tts_sentence,
                            on_tts_audio=on_tts_audio,
                            is_aborted=is_aborted,
                        )
                        if not is_aborted():
                            await on_tts_stop()
                        return (user_text, reply_text)

            learning_context["locked"] = "0"

        # Fast path: nhận diện nhanh bằng LLM hội thoại để giảm lag.
        if self._intent_detector:
            fast_intent = self._intent_detector.detect_fast(user_text)
            resolved_intent = fast_intent

            # Ưu tiên LLM cho intent luyện hội thoại nhằm sửa lỗi STT tốt hơn.
            if self._looks_like_learning_conversation_request(user_text) or fast_intent.intent in {
                "learning_conversation",
                "learning_topic",
            }:
                try:
                    llm_intent = await self._intent_detector.detect_learning_intent(user_text)
                    if llm_intent.intent in {"learning_conversation", "learning_topic"}:
                        resolved_intent = llm_intent
                except Exception as e:
                    logger.warning("LLM learning intent fallback failed: %s", e)

            if (
                learning_context
                and str(learning_context.get("mode") or "") == "vocabulary"
                and self._looks_like_continue_request(user_text)
            ):
                current_topic_id = str(learning_context.get("topic_id") or "").strip()
                selected_topic = get_topic_by_id("vocabulary", current_topic_id) if current_topic_id else None
                if selected_topic:
                    await on_tts_start()
                    start_index = self._context_next_index(learning_context)
                    reply_text, next_index, total_words = await self._teach_vocabulary_stepwise(
                        selected_topic,
                        on_tts_sentence=on_tts_sentence,
                        on_tts_audio=on_tts_audio,
                        on_learning_card=on_learning_card,
                        is_aborted=is_aborted,
                    )
                    learning_context["topic_id"] = str(selected_topic.get("id") or "")
                    learning_context["next_index"] = str(next_index)
                    learning_context["finished"] = "1" if next_index >= total_words else "0"
                    if not is_aborted():
                        await on_tts_stop()
                    return (user_text, reply_text)

            if resolved_intent.intent == "learning_conversation" or (
                resolved_intent.intent == "learning_topic"
                and resolved_intent.learning_mode == "conversation"
            ):
                await on_tts_start()
                mode, selected_topic, reply_text = self._handle_learning_intent(
                    user_text,
                    resolved_intent.intent,
                    learning_mode=resolved_intent.learning_mode,
                    topic_id=resolved_intent.topic_id,
                    learning_context=learning_context,
                )
                if mode == "vocabulary" and selected_topic:
                    start_index = 0
                    if learning_context is not None:
                        total_words = len(selected_topic.get("words") or [])
                        lock_target_index = min(total_words, start_index + LOCK_WORD_LIMIT)
                        learning_context["mode"] = "vocabulary"
                        learning_context["topic_id"] = str(selected_topic.get("id") or "")
                        learning_context["next_index"] = "0"
                        learning_context["finished"] = "0"
                        learning_context["locked"] = "1"
                        learning_context["lock_target_index"] = str(lock_target_index)

                    reply_text, next_index, total_words = await self._teach_vocabulary_stepwise(
                        selected_topic,
                        on_tts_sentence=on_tts_sentence,
                        on_tts_audio=on_tts_audio,
                        on_learning_card=on_learning_card,
                        is_aborted=is_aborted,
                        start_index=start_index,
                        batch_size=VOCAB_BATCH_SIZE,
                    )
                    if learning_context is not None:
                        learning_context["next_index"] = str(next_index)
                        learning_context["finished"] = "1" if next_index >= total_words else "0"
                        lock_target_index = self._lock_target_index(learning_context)
                        if next_index >= lock_target_index:
                            learning_context["locked"] = "0"
                else:
                    if learning_context is not None and mode == "conversation" and selected_topic:
                        learning_context["mode"] = "conversation"
                        learning_context["topic_id"] = str(selected_topic.get("id") or "")
                        learning_context["next_index"] = "0"
                        learning_context["finished"] = "0"
                        learning_context["locked"] = "1"
                        learning_context["lock_target_index"] = "0"

                    await self._speak_text(
                        reply_text,
                        on_tts_sentence=on_tts_sentence,
                        on_tts_audio=on_tts_audio,
                        is_aborted=is_aborted,
                    )
                if not is_aborted():
                    await on_tts_stop()
                return (user_text, reply_text)

            if resolved_intent.intent == "flashcard_vocab":
                await on_tts_start()
                if learning_context is not None:
                    self._start_flashcard_vocab_context(learning_context)
                reply_text = build_flashcard_start_reply()
                await self._speak_text(
                    reply_text,
                    on_tts_sentence=on_tts_sentence,
                    on_tts_audio=on_tts_audio,
                    is_aborted=is_aborted,
                    language_hint="vi",
                )
                if not is_aborted():
                    await on_tts_stop()
                return (user_text, reply_text)

            if resolved_intent.intent == "assignment":
                await on_tts_start()
                reply_text = "Hiện chưa có bài tập nào được giao."
                if assignment_provider:
                    try:
                        assignment = await assignment_provider()
                    except Exception as e:
                        logger.warning("Assignment provider failed: %s", e)
                        assignment = None
                    if assignment:
                        title = str(assignment.get("title") or "Bài tập hôm nay").strip()
                        instructions = str(assignment.get("instructions") or "").strip()
                        due_at = str(assignment.get("due_at") or "").strip()
                        due_suffix = f" Hạn nộp: {due_at}." if due_at else ""
                        reply_text = (
                            f"Bài tập của con là: {title}. "
                            f"Yêu cầu: {instructions}."
                            f"{due_suffix} "
                            f"Con hãy yêu cầu robot kiểm tra bài học nhé."
                        )
                await self._speak_text(
                    reply_text,
                    on_tts_sentence=on_tts_sentence,
                    on_tts_audio=on_tts_audio,
                    is_aborted=is_aborted,
                    language_hint="vi",
                )
                if not is_aborted():
                    await on_tts_stop()
                return (user_text, reply_text)

            if learning_context and learning_context.get("mode") in {"vocabulary", "conversation"}:
                inferred_mode = str(learning_context.get("mode"))
                selected_topic = find_topic(inferred_mode, user_text)
                if selected_topic:
                    await on_tts_start()
                    if inferred_mode == "vocabulary":
                        reply_text, next_index, total_words = await self._teach_vocabulary_stepwise(
                            selected_topic,
                            on_tts_sentence=on_tts_sentence,
                            on_tts_audio=on_tts_audio,
                            on_learning_card=on_learning_card,
                            is_aborted=is_aborted,
                            start_index=0,
                            batch_size=VOCAB_BATCH_SIZE,
                        )
                        learning_context["topic_id"] = str(selected_topic.get("id") or "")
                        learning_context["next_index"] = str(next_index)
                        learning_context["finished"] = "1" if next_index >= total_words else "0"
                    else:
                        reply_text = build_conversation_lesson(selected_topic)
                        await self._speak_text(
                            reply_text,
                            on_tts_sentence=on_tts_sentence,
                            on_tts_audio=on_tts_audio,
                            is_aborted=is_aborted,
                        )
                    if not is_aborted():
                        await on_tts_stop()
                    return (user_text, reply_text)

            if resolved_intent.intent == "music":
                logger.info("Fast music intent detected: %s", fast_intent.song_name)
                await on_tts_start()
                song_name = resolved_intent.song_name or "nhạc Việt"
                music_payload = await self._call_music_tool(
                    song_name,
                    on_music_action=on_music_action,
                )
                await self._stream_music_preview(
                    music_payload,
                    on_tts_sentence=on_tts_sentence,
                    on_tts_audio=on_tts_audio,
                    is_aborted=is_aborted,
                )
                if not is_aborted():
                    await on_tts_stop()
                return (user_text, "")
            if resolved_intent.intent == "alarm":
                logger.info("Fast alarm intent detected: %s", resolved_intent.alarm_time)
                await on_tts_start()
                if not self._mcp_tools:
                    await on_tts_sentence("MCP tool chưa sẵn sàng để báo thức.")
                    if not is_aborted():
                        await on_tts_stop()
                    return (user_text, "")

                # Call MCP set_alarm
                try:
                    args = {"time": resolved_intent.alarm_time or "", "message": resolved_intent.alarm_message or "Báo thức"}
                    tool_result = await self._mcp_tools.call_tool("set_alarm", args)
                    if tool_result.ok:
                        await on_tts_sentence(f"Đã đặt báo thức vào lúc {resolved_intent.alarm_time}.")
                    else:
                        # try to extract error text
                        err_text = "không thể đặt báo thức"
                        for it in tool_result.content:
                            if isinstance(it, dict) and it.get("type") == "text":
                                err_text = it.get("text")
                                break
                        await on_tts_sentence(f"Lỗi: {err_text}")
                except Exception as e:
                    logger.error("Alarm tool call failed: %s", e, exc_info=True)
                    await on_tts_sentence("Lỗi khi gọi tool báo thức")

                if not is_aborted():
                    await on_tts_stop()
                return (user_text, "")

        # -- Bước 2: LLM tách câu TTS (pre-fetch queue) --
        await on_tts_start()

        music_mode = {"active": False}

        response_task = asyncio.create_task(
            self._stream_response(
                user_text,
                chat_history,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                on_emotion=on_emotion,
                is_aborted=is_aborted,
                should_stop_generation=lambda: music_mode["active"],
            )
        )

        intent_task = None
        if not self._prefer_fast_only and self._intent_detector:
            intent_task = asyncio.create_task(
                self._detect_and_handle_music_intent(
                    user_text,
                    on_music_action=on_music_action,
                    on_music_detected=lambda: music_mode.__setitem__("active", True),
                )
            )

        full_response = await response_task
        music_payload = None
        if intent_task:
            music_payload = await intent_task

        if isinstance(music_payload, dict) and music_payload.get("intent") == "music":
            await self._stream_music_preview(
                music_payload,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
            )

        if not is_aborted():
            await on_tts_stop()

        return (user_text, full_response) if full_response else None

    async def _handle_roadmap_learning(
        self,
        *,
        learning_context: dict[str, str | None],
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        on_learning_card: Callable[[dict], Awaitable[None]] | None,
        is_aborted: Callable[[], bool],
    ) -> str:
        current_lesson_idx = self._context_current_lesson_id(learning_context)
        next_lesson = get_next_lesson_from_roadmap(current_lesson_idx)

        if not next_lesson:
            self._clear_learning_context(learning_context)
            reply_text = "Chúc mừng! Bạn đã hoàn thành tất cả các bài học trong lộ trình A1."
            await self._speak_text(
                reply_text,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
                language_hint="vi",
            )
            return reply_text

        lesson_type = str(next_lesson.get("lesson_type", "")).strip()
        topic_id = str(next_lesson.get("topic_id", "")).strip()

        if not lesson_type or not topic_id:
            # Invalid lesson, skip to next one to prevent infinite loop.
            learning_context["current_lesson_id"] = str(current_lesson_idx + 1)
            reply_text = "Bài học tiếp theo trong lộ trình bị lỗi. Máy tính sẽ chuyển sang bài học tiếp theo."
            await self._speak_text(
                reply_text,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
                language_hint="vi",
            )
            return reply_text

        selected_topic = get_topic_by_id(lesson_type, topic_id)
        if not selected_topic:
            learning_context["current_lesson_id"] = str(current_lesson_idx + 1)
            reply_text = f"Không tìm thấy nội dung cho chủ đề {topic_id} trong bài học này."
            await self._speak_text(
                reply_text,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
                language_hint="vi",
            )
            return reply_text

        # We have a valid lesson and topic, let's teach it.
        reply_text = ""
        if lesson_type == "vocabulary":
            reply_text, _, _ = await self._teach_vocabulary_stepwise(
                selected_topic,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                on_learning_card=on_learning_card,
                is_aborted=is_aborted,
                start_index=0,
                batch_size=VOCAB_BATCH_SIZE,
            )
        elif lesson_type == "conversation":
            reply_text = build_conversation_lesson(selected_topic)
            await self._speak_text(
                reply_text,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
            )

        # Update progress after successfully delivering the lesson
        learning_context["current_lesson_id"] = str(current_lesson_idx + 1)
        learning_context["mode"] = lesson_type
        learning_context["topic_id"] = topic_id
        learning_context["locked"] = "0"  # Don't lock, to allow user to exit
        learning_context["next_index"] = "0"
        learning_context["finished"] = "1"

        return reply_text

    @staticmethod
    def _handle_learning_intent(
        user_text: str,
        intent: str,
        *,
        learning_mode: str | None,
        topic_id: str | None,
        learning_context: dict[str, str | None] | None,
    ) -> tuple[str | None, dict | None, str]:
        mode = learning_mode
        if intent == "learning_vocab":
            mode = "vocabulary"
        elif intent == "learning_conversation":
            mode = "conversation"

        # LLM intent đôi khi trả về chủ đề learning_topic nhưng thiếu learning_mode/topic_id.
        # Ta suy luận mode/topic từ câu user gốc làm phương án dự phòng.
        inferred_topic = None
        if mode not in {"vocabulary", "conversation"}:
            if learning_context and learning_context.get("mode") in {"vocabulary", "conversation"}:
                mode = str(learning_context.get("mode"))

            vocab_topic = find_topic("vocabulary", user_text)
            conv_topic = find_topic("conversation", user_text)
            if vocab_topic:
                mode = "vocabulary"
                inferred_topic = vocab_topic
            elif conv_topic:
                mode = "conversation"
                inferred_topic = conv_topic

        if mode in {"vocabulary", "conversation"} and learning_context is not None:
            learning_context["mode"] = mode

        selected_topic = None
        if topic_id and mode in {"vocabulary", "conversation"}:
            selected_topic = get_topic_by_id(mode, topic_id)
        if selected_topic is None and inferred_topic is not None:
            selected_topic = inferred_topic
        if selected_topic is None and mode in {"vocabulary", "conversation"}:
            selected_topic = find_topic(mode, user_text)

        if selected_topic:
            if mode == "vocabulary":
                return (mode, selected_topic, build_vocab_lesson(selected_topic))
            return (mode, selected_topic, build_conversation_lesson(selected_topic))

        if mode == "vocabulary":
            return (mode, None, build_mode_suggestion("vocabulary"))
        if mode == "conversation":
            return (mode, None, build_mode_suggestion("conversation"))
        return (None, None, "Máy tính đã sẵn sàng học theo chủ đề. Bạn muốn học từ vựng hay luyện hội thoại?")

    async def _speak_text(
        self,
        text: str,
        *,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
        language_hint: str | None = None,
    ) -> None:
        await on_tts_sentence(text)
        await self._send_frames_with_pacing(
            self._tts.synthesize(text, language_hint=language_hint),
            on_tts_audio=on_tts_audio,
            is_aborted=is_aborted,
        )

    @staticmethod
    def _start_flashcard_vocab_context(learning_context: dict[str, str | None]) -> None:
        learning_context["mode"] = "flashcard_vocab"
        learning_context["topic_id"] = None
        learning_context["next_index"] = "0"
        learning_context["finished"] = "0"
        learning_context["locked"] = "0"
        learning_context["lock_target_index"] = "0"
        learning_context["attempt_count"] = "0"
        learning_context["seen_words"] = ""
        learning_context["current_lesson_id"] = "0"

    @staticmethod
    def _clear_learning_context(learning_context: dict[str, str | None]) -> None:
        learning_context["mode"] = None
        learning_context["topic_id"] = None
        learning_context["next_index"] = "0"
        learning_context["finished"] = "0"
        learning_context["locked"] = "0"
        learning_context["lock_target_index"] = "0"
        learning_context["attempt_count"] = "0"
        learning_context["seen_words"] = ""
        learning_context["current_lesson_id"] = "0"
        # Also clear teaching-specific fields so a completed lesson doesn't re-trigger
        learning_context["teaching_topic_id"] = None
        learning_context["word_index"] = "0"

    @staticmethod
    def _is_flashcard_vocab_active(learning_context: dict[str, str | None]) -> bool:
        return str(learning_context.get("mode") or "").strip() == "flashcard_vocab"

    async def _handle_flashcard_vocab_turn(
        self,
        user_text: str,
        *,
        learning_context: dict[str, str | None],
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        on_learning_card: Callable[[dict], Awaitable[None]] | None,
        is_aborted: Callable[[], bool],
    ) -> str:
        if self._looks_like_exit_learning_request(user_text):
            self._clear_learning_context(learning_context)
            reply_text = "Đã thoát chế độ học flash card. Khi nào muốn học tiếp, bạn cứ nói nhé."
            await self._speak_text(
                reply_text,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
                language_hint="vi",
            )
            return reply_text

        seen_words = self._context_seen_words(learning_context)
        if len(seen_words) >= flashcard_count():
            self._clear_learning_context(learning_context)
            reply_text = build_finish_reply()
            await self._speak_text(
                reply_text,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
                language_hint="vi",
            )
            return reply_text

        evaluation = await evaluate_flashcard_answer(self._llm, student_text=user_text)
        attempts = self._context_attempt_count(learning_context)
        feedback = str(evaluation.get("feedback_vi") or "").strip()
        is_correct = bool(evaluation.get("is_correct"))
        matched_word = str(evaluation.get("matched_word") or "").strip().lower()
        matched_card = get_flashcard_by_word(matched_word) if matched_word else None

        if is_correct and matched_card:
            if on_learning_card:
                await on_learning_card(
                    {
                        "state": "flashcard",
                        "kind": "award",
                        "word": matched_card.get("word"),
                        "meaning": matched_card.get("meaning_vi"),
                        "image_url": "/static/asset/award_320.png",
                        "duration_ms": 2000,
                    }
                )
            if matched_word not in seen_words:
                seen_words.append(matched_word)
            learning_context["seen_words"] = ",".join(seen_words)
            learning_context["next_index"] = str(len(seen_words))
            learning_context["attempt_count"] = "0"
            if len(seen_words) >= flashcard_count():
                learning_context["finished"] = "1"
                reply_text = (
                    f"Tuyệt vời! Từ {matched_card.get('word')} nghĩa là {matched_card.get('meaning_vi')}. "
                    f"{build_finish_reply()}"
                )
                self._clear_learning_context(learning_context)
            else:
                reply_text = (
                    f"Tuyệt vời! Từ {matched_card.get('word')} nghĩa là {matched_card.get('meaning_vi')}. "
                    f"{build_next_card_prompt()}"
                )
        else:
            attempts += 1
            learning_context["attempt_count"] = str(attempts)
            unknown_word = str(evaluation.get("unknown_word") or "").strip()
            unknown_meaning_vi = str(evaluation.get("unknown_meaning_vi") or "").strip()
            if unknown_word:
                learning_context["attempt_count"] = "0"
                if feedback:
                    reply_text = feedback
                elif unknown_meaning_vi:
                    reply_text = (
                        f"Từ {unknown_word} nghĩa là {unknown_meaning_vi}. "
                        f"{build_next_card_prompt()}"
                    )
                else:
                    reply_text = (
                        f"Từ {unknown_word} là một từ tiếng Anh. "
                        f"{build_next_card_prompt()}"
                    )
            elif attempts >= 3:
                learning_context["attempt_count"] = "0"
                reply_text = (
                    "Bạn đã đoán sai quá 3 lần rồi. Robot chuyển sang một từ khác hoặc kết thúc nhé."
                )
            else:
                reply_text = (
                    f"{feedback} Bạn hãy thử lại một lần nữa xem."
                )

        await self._speak_text(
            reply_text,
            on_tts_sentence=on_tts_sentence,
            on_tts_audio=on_tts_audio,
            is_aborted=is_aborted,
            language_hint="vi",
        )
        return reply_text

    async def _teach_vocabulary_stepwise(
        self,
        topic: dict,
        *,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        on_learning_card: Callable[[dict], Awaitable[None]] | None,
        is_aborted: Callable[[], bool],
        start_index: int = 0,
        batch_size: int = VOCAB_BATCH_SIZE,
    ) -> tuple[str, int, int]:
        total_words = len(topic.get("words") or [])
        steps = build_vocab_lesson_steps(topic, max_words=batch_size, start_index=start_index)
        spoken_lines: list[str] = []
        for step in steps:
            if is_aborted():
                break
            flashcard_payload = step.get("flashcard") if isinstance(step, dict) else None
            if flashcard_payload and on_learning_card:
                await on_learning_card(flashcard_payload)
            speech = str(step.get("speech") or "").strip() if isinstance(step, dict) else ""
            if not speech:
                continue
            spoken_lines.append(speech)
            await on_tts_sentence(speech)
            await self._send_frames_with_pacing(
                self._tts.synthesize(speech, language_hint="vi"),
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
            )
            await asyncio.sleep(self._tts.frame_duration_s * 2)
        consumed = sum(1 for step in steps if isinstance(step, dict) and step.get("flashcard"))
        next_index = min(total_words, max(0, start_index) + consumed)
        return (" ".join(spoken_lines).strip(), next_index, total_words)

    @staticmethod
    def _looks_like_learning_conversation_request(text: str) -> bool:
        lowered = (text or "").lower()
        learning_markers = (
            "hội thoại",
            "hoi thoai",
            "luyện",
            "luyen",
            "giao tiếp",
            "giao tiep",
        )
        return any(marker in lowered for marker in learning_markers)

    @staticmethod
    def _looks_like_roadmap_request(text: str) -> bool:
        lowered = (text or "").lower()
        final_markers = (
            "lộ trình",
            "lo trinh",
            "bài học",
            "bai hoc",
            "bài mới",
            "bai moi",
            "học tiếp theo",
            "hoc tiep theo",
        )
        return any(marker in lowered for marker in final_markers)

    @staticmethod
    def _looks_like_continue_request(text: str) -> bool:
        lowered = (text or "").lower()
        continue_markers = (
            "học tiếp",
            "hoc tiep",
            "tiếp tục",
            "tiep tuc",
            "học nữa",
            "hoc nua",
            "tiếp nữa",
            "tiep nua",
        )
        return any(marker in lowered for marker in continue_markers)

    @staticmethod
    def _looks_like_exit_learning_request(text: str) -> bool:
        lowered = (text or "").lower()
        exit_markers = (
            "thoát",
            "thoat",
            "dừng học",
            "dung hoc",
            "kết thúc",
            "ket thuc",
            "đổi chủ đề",
            "doi chu de",
            "chuyển chủ đề",
            "chuyen chu de",
        )
        return any(marker in lowered for marker in exit_markers)

    @staticmethod
    def _is_learning_locked(learning_context: dict[str, str | None]) -> bool:
        return str(learning_context.get("locked") or "0").strip() in {"1", "true", "yes", "on"}

    @staticmethod
    def _looks_like_learning_followup(text: str, learning_context: dict[str, str | None]) -> bool:
        lowered = (text or "").lower()
        markers = (
            "học",
            "hoc",
            "tiếp",
            "tiep",
            "nhắc lại",
            "nhac lai",
            "từ",
            "tu",
            "vựng",
            "vung",
            "hội thoại",
            "hoi thoai",
            "chủ đề",
            "chu de",
            "đọc lại",
            "doc lai",
        )
        if any(m in lowered for m in markers):
            return True

        topic_id = str(learning_context.get("topic_id") or "").strip().lower()
        if topic_id and topic_id in lowered:
            return True
        return False

    @staticmethod
    def _lock_target_index(learning_context: dict[str, str | None]) -> int:
        raw = str(learning_context.get("lock_target_index") or "0").strip()
        try:
            value = int(raw)
        except Exception:
            return LOCK_WORD_LIMIT
        return value if value > 0 else LOCK_WORD_LIMIT

    @staticmethod
    def _context_next_index(learning_context: dict[str, str | None]) -> int:
        raw = str(learning_context.get("next_index") or "0").strip()
        try:
            return max(0, int(raw))
        except Exception:
            return 0

    async def _handle_teaching_mode(
        self,
        user_text: str,
        *,
        learning_context: dict[str, str | None],
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        on_emotion: Callable[[str], Awaitable[None]] | None = None,
        is_aborted: Callable[[], bool],
        game_profile: GameProfile | None = None,
        device_id: str | None = None,
    ) -> str:
        """
        Adaptive teaching — LLM-driven conversation.

        Flow:
        1. Load lesson + current word from learning_context
        2. Build conversation prompt (inject current_word, history, student_input)
        3. Call LLM → parse {text, emotion, language, advance, wait_for_student}
        4. Scoring backstop validates advance decision
        5. Speak the response
        6. If advance=true: word_index += 1
        7. If all words done: advance to next roadmap lesson
        8. If wait_for_student=true: return (pipeline ends, wait for student)
        9. If wait_for_student=false: continue recursively (robot keeps talking)
        """
        content_service = get_teaching_content_service()
        teaching_engine = AdaptiveTeachingEngine()

        # ── Onboarding: name extraction ──
        if learning_context.get("onboarding_state") == "asked_name":
            return await self._handle_onboarding(
                user_text, content_service,
                learning_context, device_id,
                on_tts_sentence, on_tts_audio, is_aborted,
            )

        # ── Ensure active topic ──
        if not learning_context.get("teaching_topic_id"):
            await self._init_lesson(learning_context, user_text, content_service, device_id)

        topic_id = str(learning_context.get("teaching_topic_id") or "")
        topic = content_service.get_topic(topic_id) if topic_id else None
        if not topic:
            reply = "Xin lỗi, chưa có nội dung giảng dạy nào được tải."
            await self._speak_text(reply, on_tts_sentence=on_tts_sentence, on_tts_audio=on_tts_audio, is_aborted=is_aborted, language_hint="vi")
            return reply

        words = topic.get("vocabulary") or []
        if not words:
            reply = "Bài học chưa có từ vựng. Hãy chọn bài khác nhé!"
            await self._speak_text(reply, on_tts_sentence=on_tts_sentence, on_tts_audio=on_tts_audio, is_aborted=is_aborted, language_hint="vi")
            return reply

        word_index = self._ctx_word_index(learning_context)
        total_words = len(words)

        # ── Lesson complete check ──
        if word_index >= total_words:
            await self._advance_to_next_lesson(
                learning_context, topic_id, device_id, content_service,
                on_tts_sentence, on_tts_audio, is_aborted,
            )
            return ""

        current_vocab = words[word_index]
        next_vocab = teaching_engine.get_next_teaching_word(words, word_index)

        # ── Get land/module context ──
        current_lesson_id = self._context_current_lesson_id(learning_context)
        from app.services.learning_content import get_a1_learning_roadmap
        from app.services.story_engine import get_land_by_module_index
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

        is_continuation = user_text.strip() == "__CONTINUATION__"
        valid_input = teaching_engine.student_input_is_valid(user_text)

        # ── Track attempts on this word ──
        attempt_raw = learning_context.get("_fail_count", "0")
        try:
            fail_count = int(attempt_raw)
        except (ValueError, TypeError):
            fail_count = 0
        if valid_input and not is_continuation:
            fail_count += 1
        attempt_hint = f"\n[Attempt {fail_count} of 3]" if fail_count > 0 else ""

        # ── Conversation history for context (prevents repetition) ──
        if not hasattr(self, '_teaching_history'):
            self._teaching_history = []
        conv_history = self._teaching_history

        # ── Build conversation prompt ──
        prompt = teaching_engine.build_teaching_prompt(
            student_input=user_text,
            current_word=str(current_vocab.get("word", "")),
            word_meaning=str(current_vocab.get("meaning_vi", "")),
            word_examples=current_vocab.get("examples", []),
            word_objects=current_vocab.get("related_objects", []),
            next_word=str(next_vocab.get("word", "")) if next_vocab else None,
            next_word_meaning=str(next_vocab.get("meaning_vi", "")) if next_vocab else None,
            word_index=word_index,
            total_words=total_words,
            conversation_history=conv_history[-6:] if not is_continuation else None,
            land_name=land_name if word_index == 0 else "",
            player_name=str(learning_context.get("player_name") or ""),
        )

        xp_str = str(game_profile.total_xp) if game_profile else "0"
        level_str = str(game_profile.level) if game_profile else "1"
        streak_str = str(game_profile.streak) if game_profile else "0"
        story_note = f"\n[Story: Land={land_name}, XP={xp_str}, Level={level_str}, Streak={streak_str}]{attempt_hint}"

        # ── Call LLM ──
        llm_response = await self._llm.chat_json(
            user_text=prompt + story_note,
            system_prompt=TEACHING_SYSTEM_PROMPT,
        )

        if not llm_response:
            reply_text = "Xin lỗi, robot đang gặp chút vấn đề. Chúng ta thử lại nhé!"
            await self._speak_text(reply_text, on_tts_sentence=on_tts_sentence, on_tts_audio=on_tts_audio, is_aborted=is_aborted, language_hint="vi")
            return reply_text

        # ── Parse LLM response ──
        if isinstance(llm_response, dict):
            reply_text = str(llm_response.get("text", "")).strip()
            response_emotion = str(llm_response.get("emotion", "neutral")).strip().lower()
            llm_advance = bool(llm_response.get("advance", False))
            wait_for_student = bool(llm_response.get("wait_for_student", True))
            response_language = str(llm_response.get("language", "vi")).strip().lower()
            if response_language not in ("vi", "en"):
                response_language = "vi"
        else:
            reply_text = str(llm_response).strip()
            response_emotion = "neutral"
            response_language = "vi"
            llm_advance = False
            wait_for_student = True

        if not reply_text:
            reply_text = "Con thử nói lại xem nhé!"
            wait_for_student = True

        # Emotion to ESP32
        if response_emotion in ("neutral", "happy", "encouraging", "praising", "confused", "excited") and on_emotion and not is_aborted():
            await on_emotion(response_emotion)

        # ── Scoring backstop ──
        current_word_text = str(current_vocab.get("word", ""))
        should_advance = llm_advance
        if valid_input and current_word_text and not is_continuation:
            backstop = teaching_engine.evaluate_with_backstop(
                student_text=user_text,
                expected_answer=current_word_text,
                llm_advance=llm_advance,
            )
            should_advance = backstop["advance"]
            if backstop["method"] not in ("llm_only", "llm_decided"):
                logger.info("Backstop %s: word=%s input=%s conf=%.3f",
                           backstop["method"], current_word_text, user_text, backstop["confidence"])

            # Nếu backstop override LLM → gọi lại LLM với context đúng
            if should_advance != llm_advance:
                if should_advance:
                    note = "[Note: The student said the word correctly. Advance to next word and introduce it.]"
                else:
                    note = "[Note: The student did NOT say it correctly. Stay on current word, re-explain, ask again.]"
                llm_response = await self._llm.chat_json(
                    user_text=prompt + story_note + "\n" + note,
                    system_prompt=TEACHING_SYSTEM_PROMPT,
                )
                if isinstance(llm_response, dict):
                    reply_text = str(llm_response.get("text", "")).strip()
                    response_emotion = str(llm_response.get("emotion", "neutral")).strip().lower()
                    wait_for_student = bool(llm_response.get("wait_for_student", True))
                    response_language = str(llm_response.get("language", "vi")).strip().lower()
                    if response_language not in ("vi", "en"):
                        response_language = "vi"
                if not reply_text:
                    reply_text = "Giỏi lắm! Từ tiếp theo nhé!" if should_advance else "Thử lại nha!"
                    wait_for_student = True

        # ── Game profile update (XP, streak) ──
        if game_profile and valid_input and not is_continuation:
            try:
                is_first_try = word_index == self._ctx_word_index(learning_context)
                if should_advance:
                    game_profile.add_correct_answer(is_first_try=is_first_try)
                else:
                    game_profile.add_wrong_answer()
            except Exception as e:
                logger.warning("Game profile update error: %s", e)

        # ── Auto-skip after 3 fails ──
        if not should_advance and fail_count >= 3:
            should_advance = True
            reply_text = f"Không sao! Mình chuyển sang từ khác nha, lát quay lại từ {current_word_text} sau!"
            wait_for_student = True
            logger.info("Auto-skip word '%s' after %d failed attempts", current_word_text, fail_count)

        # ── Advance logic ──
        if should_advance:
            word_index += 1
            learning_context["word_index"] = str(word_index)
            learning_context["_fail_count"] = "0"  # reset on new word
            if word_index < total_words:
                wait_for_student = True
        else:
            learning_context["_fail_count"] = str(fail_count)
            wait_for_student = True

        # ── Save conversation history ──
        if valid_input and not is_continuation:
            conv_history.append({"role": "user", "content": user_text})
        conv_history.append({"role": "assistant", "content": reply_text})
        if len(conv_history) > 20:
            conv_history[:] = conv_history[-20:]

        # ── Enforce: no consecutive teacher turns ──
        last_wait = learning_context.get("_last_wait", "true")
        if not wait_for_student and last_wait == "false":
            wait_for_student = True
        learning_context["_last_wait"] = "true" if wait_for_student else "false"

        # ── Speak ──
        await self._speak_teaching_text(
            reply_text,
            on_tts_sentence=on_tts_sentence,
            on_tts_audio=on_tts_audio,
            is_aborted=is_aborted,
            default_language=response_language,
        )

        # ── Handle continuation or lesson advance ──
        if word_index >= total_words:
            await self._advance_to_next_lesson(
                learning_context, topic_id, device_id, content_service,
                on_tts_sentence, on_tts_audio, is_aborted,
            )
        elif not wait_for_student and not is_aborted():
            logger.info("Teacher continuation: auto-triggering next teaching turn")
            await self._handle_teaching_mode(
                "__CONTINUATION__",
                learning_context=learning_context,
                on_tts_sentence=on_tts_sentence,
                on_tts_audio=on_tts_audio,
                on_emotion=on_emotion,
                is_aborted=is_aborted,
                game_profile=game_profile,
                device_id=device_id,
            )

        return reply_text

    async def _handle_onboarding(
        self,
        user_text: str,
        content_service,
        learning_context: dict[str, str | None],
        device_id: str | None,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
    ) -> str:
        """Extract name, set up first lesson."""
        name_clean = user_text.strip()
        prefixes = [
            "tên con là", "tên tớ là", "tên mình là", "tên em là", "con tên là",
            "tớ tên là", "mình tên là", "em tên là", "mình là", "tớ là", "con là",
            "em là", "tên là", "là"
        ]
        for p in prefixes:
            if name_clean.lower().startswith(p):
                name_clean = name_clean[len(p):].strip()
                break
        name_clean = name_clean.rstrip(".?!,")
        words = name_clean.split()
        if len(words) > 3:
            try:
                xp = f"Trích xuất tên của học sinh từ câu nói sau. Chỉ trả về duy nhất tên, không thêm gì. Câu: \"{user_text}\""
                extracted = await self._llm.chat_json(xp, system_prompt="Bạn là trợ lý trích xuất tên riêng.")
                if isinstance(extracted, dict) and extracted.get("name"):
                    name_clean = str(extracted["name"])
                elif isinstance(extracted, str) and extracted:
                    name_clean = extracted
            except Exception:
                name_clean = words[-1]
        player_name = name_clean.title() if name_clean else "Nhà thám hiểm"
        learning_context["player_name"] = player_name
        learning_context["onboarding_state"] = ""

        matched_topic = content_service.get_topic("greetings_basic") or content_service.get_topic("colors_basic")
        if matched_topic:
            learning_context["teaching_topic_id"] = matched_topic["topic_id"]
            learning_context["word_index"] = "0"

        if device_id:
            try:
                save_lesson_progress(device_id, learning_context)
            except Exception as e:
                logger.warning("Failed to save onboarding: %s", e)

        reply = f"Rất vui được thám hiểm cùng {player_name}! Chúng ta cùng bắt đầu bài học đầu tiên nhé!"
        await self._speak_teaching_text(reply, on_tts_sentence=on_tts_sentence, on_tts_audio=on_tts_audio, is_aborted=is_aborted, default_language="vi")
        return reply

    async def _init_lesson(
        self,
        learning_context: dict[str, str | None],
        user_text: str,
        content_service,
        device_id: str | None,
    ) -> None:
        """Initialize or resume lesson from roadmap."""
        from app.services.learning_content import get_next_lesson_from_roadmap
        current_lesson_id = self._context_current_lesson_id(learning_context)
        roadmap_lesson = get_next_lesson_from_roadmap(current_lesson_id)
        matched_topic = None
        if roadmap_lesson:
            default_topic_id = str(roadmap_lesson.get("topic_id") or "")
            if default_topic_id:
                matched_topic = content_service.get_topic(default_topic_id)
        if not matched_topic and user_text.strip():
            user_lower = user_text.lower()
            for t in content_service.get_all_topics():
                if t.get("title", "").lower() in user_lower or t.get("topic_id", "").lower() in user_lower:
                    matched_topic = t
                    break
        if not matched_topic:
            matched_topic = content_service.get_topic("colors_basic")
        if matched_topic:
            learning_context["teaching_topic_id"] = matched_topic["topic_id"]
            learning_context["word_index"] = "0"
            logger.info("Initialized lesson: %s", matched_topic["topic_id"])

    async def _advance_to_next_lesson(
        self,
        learning_context: dict[str, str | None],
        topic_id: str,
        device_id: str | None,
        content_service,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
    ) -> None:
        """Mark current lesson complete and advance."""
        from app.services.learning_content import get_a1_learning_roadmap, get_next_lesson_from_roadmap
        from app.services.story_engine import get_land_by_module_index, STORY_OUTRO
        current_flat_id = self._context_current_lesson_id(learning_context)
        next_lesson = get_next_lesson_from_roadmap(current_flat_id + 1)
        if device_id:
            try:
                mark_lesson_completed(device_id, topic_id)
            except Exception as e:
                logger.warning("Failed to mark lesson completed: %s", e)
        if next_lesson:
            roadmap = get_a1_learning_roadmap()
            modules = roadmap.get("modules") or roadmap.get("units") or []
            flat_idx = -1
            next_module_idx = 0
            for mi, mod in enumerate(modules):
                for li in range(len(mod.get("lessons", []))):
                    flat_idx += 1
                    if flat_idx == current_flat_id + 1:
                        next_module_idx = mi
                        break
                if flat_idx == current_flat_id + 1:
                    break
            next_topic_id = str(next_lesson.get("topic_id") or "")
            next_topic = content_service.get_topic(next_topic_id) if next_topic_id else None
            if next_topic:
                learning_context["current_lesson_id"] = str(current_flat_id + 1)
                learning_context["teaching_topic_id"] = next_topic_id
                learning_context["word_index"] = "0"
                land = get_land_by_module_index(next_module_idx)
                land_name = land["name"] if land else ""
                reply = f"Giỏi quá! Chuyển sang bài mới ở {land_name}. Học từ đầu tiên nhé!"
            else:
                learning_context["current_lesson_id"] = str(current_flat_id + 1)
                learning_context["teaching_topic_id"] = None
                reply = "Bài tiếp theo chưa sẵn sàng. Nói 'tiếp theo' khi muốn học nhé!"
        else:
            learning_context["teaching_topic_id"] = None
            reply = STORY_OUTRO
        await self._speak_teaching_text(reply, on_tts_sentence=on_tts_sentence, on_tts_audio=on_tts_audio, is_aborted=is_aborted, default_language="vi")

    @staticmethod
    def _ctx_word_index(learning_context: dict[str, str | None]) -> int:
        raw = str(learning_context.get("word_index") or "0").strip()
        try:
            return max(0, int(raw))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _context_attempt_count(learning_context: dict[str, str | None]) -> int:
        raw = str(learning_context.get("attempt_count") or "0").strip()
        try:
            return max(0, int(raw))
        except Exception:
            return 0

    @staticmethod
    def _detect_sentence_language(text: str, default: str = "vi") -> str:
        """
        Detect whether a sentence is primarily English or Vietnamese.
        Uses word-level heuristic: if >75% of word tokens are pure ASCII (a-z),
        treat the sentence as English. Vietnamese text mixed with a few English
        words (e.g. "Bạn thử nói 'cat' xem!") stays classified as Vietnamese.
        """
        words = re.findall(r"[a-zA-Z\u00C0-\u1EF9]+", text)
        if not words:
            return default
        ascii_words = sum(1 for w in words if all(c.isascii() for c in w))
        ratio = ascii_words / len(words)
        return "en" if ratio > 0.75 else "vi"

    async def _speak_teaching_text(
        self,
        text: str,
        *,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
        default_language: str = "vi",
    ) -> None:
        """
        Speak teaching text entirely in `default_language` (Vietnamese).
        No per-sentence language detection — the TTS SSML layer handles
        English word pronunciation internally via <voice> tags, avoiding
        the choppiness that comes from splitting into vi/en chunks.
        """
        text = text.strip()
        if not text or is_aborted():
            return
        await self._speak_text(
            text,
            on_tts_sentence=on_tts_sentence,
            on_tts_audio=on_tts_audio,
            is_aborted=is_aborted,
            language_hint=default_language,
        )

    @staticmethod
    def _context_seen_words(learning_context: dict[str, str | None]) -> list[str]:
        raw = str(learning_context.get("seen_words") or "").strip()
        return [word.strip().lower() for word in raw.split(",") if word.strip()]

    @staticmethod
    def _context_current_lesson_id(learning_context: dict[str, str | None]) -> int:
        raw = str(learning_context.get("current_lesson_id") or "0").strip()
        try:
            return max(0, int(raw))
        except Exception:
            return 0

    async def _detect_and_handle_music_intent(
        self,
        user_text: str,
        *,
        on_music_action: Callable[[dict], Awaitable[None]],
        on_music_detected: Callable[[], None],
    ) -> dict | None:
        """Detect intent song song với LLM chính, gọi tool nhạc."""
        if not self._intent_detector:
            return None

        try:
            intent = await self._intent_detector.detect(user_text)
            if intent.intent != "music":
                await on_music_action({"intent": "other"})
                return {"intent": "other"}

            on_music_detected()

            if not self._mcp_tools:
                payload = {
                    "intent": "music",
                    "song_name": intent.song_name,
                    "ok": False,
                    "error": "MCP tool registry chưa sẵn sàng",
                }
                await on_music_action(payload)
                return payload

            song_name = intent.song_name or "nhạc Việt"
            payload = await self._call_music_tool(
                song_name,
                on_music_action=on_music_action,
            )
            return payload
        except Exception as e:
            logger.error("Intent flow failed: %s", e, exc_info=True)
            payload = {
                "intent": "other",
                "ok": False,
                "error": str(e),
            }
            await on_music_action(payload)
            return payload

    async def _stream_response(
        self,
        user_text: str,
        chat_history: list[dict],
        *,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        on_emotion: Callable[[str], Awaitable[None]] | None = None,
        is_aborted: Callable[[], bool],
        should_stop_generation: Callable[[], bool],
    ) -> str:
        """
        LLM streaming -> tách câu -> TTS pre-fetch -> gửi audio.
        Producer: LLM chunks -> sentences -> TTS opus frames -> queue
        Consumer: queue -> on_tts_audio (gửi ESP32)
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        full_response = ""
        producer_error = None

        async def producer():
            nonlocal full_response, producer_error
            try:
                raw_response_parts: list[str] = []
                async for chunk in self._llm.chat_stream(user_text, chat_history):
                    if is_aborted() or should_stop_generation():
                        break
                    raw_response_parts.append(chunk)

                raw_response = "".join(raw_response_parts).strip()
                if not raw_response:
                    return

                # Sửa lỗi Regex re.sub tại đây để bóc tách thông tin
                response_language, response_emotion, response_text = self._parse_llm_tts_payload(raw_response)
                full_response = response_text
                if response_emotion and on_emotion and not is_aborted() and not should_stop_generation():
                    await on_emotion(response_emotion)

                buffer = response_text
                while True:
                    sentence, buffer = self._extract_sentence(buffer)
                    if not sentence:
                        break
                    await self._enqueue_sentence(
                        sentence,
                        queue,
                        on_tts_sentence,
                        is_aborted,
                        language=response_language,
                    )

                while len(buffer) >= CHUNK_HARD_LIMIT and not is_aborted() and not should_stop_generation():
                    text_chunk, buffer = self._extract_soft_chunk(buffer)
                    if not text_chunk:
                        break
                    await self._enqueue_sentence(
                        text_chunk,
                        queue,
                        on_tts_sentence,
                        is_aborted,
                        language=response_language,
                    )

                remaining = buffer.strip()
                if remaining and not is_aborted() and not should_stop_generation():
                    await self._enqueue_sentence(
                        remaining,
                        queue,
                        on_tts_sentence,
                        is_aborted,
                        language=response_language,
                    )
            except Exception as e:
                producer_error = e
                logger.error(f"Producer error: {e}", exc_info=True)
            finally:
                await queue.put(_DONE)

        async def consumer():
            total_frames = 0
            # Gửi trước một vài frames làm pre-buffer trên ESP32 để chống nhiễu lag.
            PRE_BUFFER = 3
            FRAME_S = self._tts.frame_duration_s
            PACE = 1.0      # Điều tốc phát 1:1 với tốc độ sinh audio thực tế
            GRACE_S = 0.05  # Thêm 50ms giữa các câu để đồng bộ luồng
            next_send_ts: float | None = None
            has_spoken_sentence = False
            loop = asyncio.get_running_loop()

            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                if is_aborted() or should_stop_generation():
                    continue  # drain queue

                # Sentence marker: gửi sentence_start SAU KHI audio câu trước đã gửi hết
                if isinstance(item, tuple) and item[0] == _SENTENCE_MARKER:
                    if has_spoken_sentence:
                        # Đợi thêm 1 frame + grace thời gian phát để tránh chồng lấn
                        await asyncio.sleep(FRAME_S + GRACE_S)

                    await on_tts_sentence(item[1])
                    has_spoken_sentence = True
                    continue

                if isinstance(item, bytes):
                    await on_tts_audio(item)
                total_frames += 1

                # Pacing: Đảm bảo không gửi nhanh hơn tốc độ phát thực tế
                if total_frames == PRE_BUFFER:
                    next_send_ts = loop.time() + FRAME_S * PACE
                elif total_frames > PRE_BUFFER and next_send_ts is not None:
                    now = loop.time()
                    if now < next_send_ts:
                        await asyncio.sleep(next_send_ts - now)
                    next_send_ts += FRAME_S * PACE

            logger.info(f"\033[92m Sent total {total_frames} opus frames\033[0m")

        # Chạy song song: producer TTS câu tiếp, consumer gửi câu hiện tại
        await asyncio.gather(producer(), consumer())

        if producer_error:
            logger.error(f"Pipeline had producer error: {producer_error}")

        return full_response

    async def _stream_music_preview(
        self,
        music_payload: dict,
        *,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
    ) -> None:
        """Phát preview nhạc thực tế từ Deezer khi intent là music."""
        if not self._mcp_tools or is_aborted():
            return

        requested_song = str(music_payload.get("song_name") or "bài nhạc").strip()
        tracks = self._extract_tracks(music_payload)
        if not tracks:
            await on_tts_sentence("Mình chưa tìm thấy bài nhạc phù hợp, nguồn nhạc gặp sự cố.")
            await self._send_frames_with_pacing(
                self._tts.synthesize("Mình chưa tìm thấy bài nhạc phù hợp."),
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
            )
            return

        first = tracks[0]
        title = str(first.get("title") or music_payload.get("song_name") or "bài nhạc")
        artist = str(first.get("artist") or "")
        preview_url = str(first.get("preview_url") or "").strip()

        if artist:
            ack = f"Đang mở bài {title} của {artist}."
        else:
            ack = f"Đang mở bài {title}."
        await on_tts_sentence(ack)
        await self._send_frames_with_pacing(
            self._tts.synthesize(ack),
            on_tts_audio=on_tts_audio,
            is_aborted=is_aborted,
        )

        # Tiến hành stream bài hát nếu tìm thấy, mặc định phát preview 30s.
        streamed = await self._send_frames_with_pacing(
            self._tts.stream_full_song_by_query(f"{title} {artist}".strip()),
            on_tts_audio=on_tts_audio,
            is_aborted=is_aborted,
        )

        # Fallback: nếu không stream được full song thì phát preview 30s bằng URL.
        if streamed == 0 and preview_url:
            streamed = await self._send_frames_with_pacing(
                self._tts.stream_audio_url(preview_url),
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
            )

        if streamed == 0:
            await on_tts_sentence("Xin lỗi, hệ thống chưa phát được bài này.")
            await self._send_frames_with_pacing(
                self._tts.synthesize("Xin lỗi, hệ thống chưa phát được bài này."),
                on_tts_audio=on_tts_audio,
                is_aborted=is_aborted,
            )

    async def _send_frames_with_pacing(
        self,
        frame_stream,
        *,
        on_tts_audio: Callable[[bytes], Awaitable[None]],
        is_aborted: Callable[[], bool],
    ) -> int:
        """Gửi Opus frames theo tốc độ phát audio thực tế tránh tràn buffer ESP32."""
        pre_buffer = 3
        frame_s = self._tts.frame_duration_s
        pace = 1.0
        next_send_ts: float | None = None
        sent = 0
        loop = asyncio.get_running_loop()

        async for opus_frame in frame_stream:
            if is_aborted():
                return 0

            await on_tts_audio(opus_frame)
            sent += 1

            if sent == pre_buffer:
                next_send_ts = loop.time() + frame_s * pace
            elif sent > pre_buffer and next_send_ts is not None:
                now = loop.time()
                if now < next_send_ts:
                    await asyncio.sleep(next_send_ts - now)
                next_send_ts += frame_s * pace
        return sent

    async def _call_music_tool(
        self,
        song_name: str,
        *,
        on_music_action: Callable[[dict], Awaitable[None]],
    ) -> dict:
        """Gửi lệnh gọi tool tìm nhạc và trả về payload phản hồi."""
        if not self._mcp_tools:
            payload = {
                "intent": "music",
                "song_name": song_name,
                "ok": False,
                "error": "MCP tool registry chưa sẵn sàng",
            }
            await on_music_action(payload)
            return payload

        request_body = {
            "song_name": song_name,
            "query": song_name,
            "limit": 5,
        }
        tool_result = await self._mcp_tools.call_tool(
            "search_vietnamese_music", request_body
        )

        payload = {
            "intent": "music",
            "song_name": song_name,
            "request_body": request_body,
            "ok": tool_result.ok,
            "content": tool_result.content,
        }
        await on_music_action(payload)
        return payload

    @staticmethod
    def _extract_tracks(music_payload: dict) -> list[dict]:
        """Trích xuất tracks từ payload tool result."""
        content = music_payload.get("content")
        if not isinstance(content, list):
            return []

        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "json":
                continue
            data = item.get("json")
            if not isinstance(data, dict):
                continue
            tracks = data.get("tracks")
            if isinstance(tracks, list):
                return [t for t in tracks if isinstance(t, dict)]
        return []

    async def _enqueue_sentence(
        self,
        sentence: str,
        queue: asyncio.Queue,
        on_tts_sentence: Callable[[str], Awaitable[None]],
        is_aborted: Callable[[], bool],
        *,
        language: str | None = None,
    ) -> None:
        """TTS 1 câu và đẩy từng opus frame vào queue."""
        logger.info(f"\033[92m  TTS[{language or 'auto'}]: {sentence}\033[0m")
        # Gửi sentence marker qua queue để đồng bộ với audio frames
        await queue.put((_SENTENCE_MARKER, sentence))

        frame_count = 0
        async for opus_frame in self._tts.synthesize(
            sentence, language_hint=language
        ):
            if is_aborted():
                break
            await queue.put(opus_frame)
            frame_count += 1
        logger.info(f"\033[92m   Queued {frame_count} frames for: {sentence[:40]}\033[0m")

    @staticmethod
    def _parse_llm_tts_payload(raw_response: object) -> tuple[str | None, str | None, str]:
        """
        Parse response format:
        {"language":"vi|en","emotion":"neutral|happy|sad|excited|confused|sleepy|laughing|loving","text":"..."}
        Fallback: treat raw as plain text.
        """
        def _extract_payload(obj: dict) -> tuple[str | None, str | None, str] | None:
            text_val = obj.get("text")
            if not isinstance(text_val, str):
                return None
            lang_val = str(obj.get("language", "")).strip().lower()
            if lang_val not in {"vi", "en"}:
                lang_val = None
            emotion_val = str(obj.get("emotion", "")).strip().lower()
            if emotion_val not in {"neutral", "happy", "sad", "excited", "confused", "sleepy", "laughing", "loving"}:
                emotion_val = None
            return (lang_val, emotion_val, text_val.strip())

        if isinstance(raw_response, dict):
            payload = _extract_payload(raw_response)
            if payload:
                return payload
            return (None, None, "")

        raw = str(raw_response or "").strip()
        if not raw:
            return (None, None, "")

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = _extract_payload(parsed)
                if payload:
                    return payload
        except Exception:
            pass

        fenced = re.sub(
            r"^http://googleusercontent.com/immersive_entry_chip/0\s*",
            "",
            raw,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        try:
            parsed = json.loads(fenced)
            if isinstance(parsed, dict):
                payload = _extract_payload(parsed)
                if payload:
                    return payload
        except Exception:
            pass

        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = raw[start : end + 1]
            try:
                parsed = json.loads(snippet)
                if isinstance(parsed, dict):
                    payload = _extract_payload(parsed)
                    if payload:
                        return payload
            except Exception:
                pass

        return (None, None, raw)

    @staticmethod
    def _extract_sentence(buffer: str) -> tuple[str | None, str]:
        """Tách câu đầu tiên hoàn chỉnh từ buffer."""
        for i, char in enumerate(buffer):
            if char in SENTENCE_ENDINGS:
                sentence = buffer[: i + 1].strip()
                remaining = buffer[i + 1 :]
                if sentence and len(sentence) > 1:
                    return sentence, remaining
                return None, remaining
        return None, buffer

    @staticmethod
    def _extract_soft_chunk(buffer: str) -> tuple[str | None, str]:
        """Cắt một chunk nhỏ khi chưa có dấu câu để giảm độ trễ TTS."""
        if len(buffer) < CHUNK_MIN_CHARS:
            return None, buffer

        limit = min(len(buffer), CHUNK_HARD_LIMIT)
        punct_cut = -1
        for i in range(limit - 1, CHUNK_MIN_CHARS - 1, -1):
            if buffer[i] in CHUNK_PUNCT_BREAKS:
                punct_cut = i
                break

        if punct_cut != -1:
            chunk = buffer[: punct_cut + 1].rstrip()
            remaining = buffer[punct_cut + 1 :].lstrip()
        else:
            # fallback: cắt ở khoảng trắng, KHÔNG cắt giữa từ
            space_cut = buffer.rfind(CHUNK_SPACE_BREAK, CHUNK_MIN_CHARS, limit)
            if space_cut == -1:
                return None, buffer
            chunk = buffer[:space_cut].rstrip()
            remaining = buffer[space_cut + 1 :].lstrip()

        if len(chunk) < CHUNK_MIN_CHARS:
            return None, buffer
        return chunk, remaining
