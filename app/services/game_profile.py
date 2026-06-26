"""
Game Profile — XP, Levels, Badges, Quests.

Gắn liền với mỗi robot (mỗi học sinh có profile riêng).
Dữ liệu persistence qua database layer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

from app.server_logging import get_logger

logger = get_logger(__name__)

# ─── Constants ─────────────────────────────────────────────────

XP_PER_LEVEL = 100
XP_CORRECT_ANSWER = 10
XP_CORRECT_FIRST_TRY = 20  # bonus khi trả lời đúng ngay lần đầu
XP_STREAK_BONUS = 5  # bonus khi streak >= 3
XP_LESSON_COMPLETE = 50
XP_QUEST_COMPLETE = 100

STREAK_THRESHOLD = 3  # số câu đúng liên tiếp để nhận streak bonus
MAX_LEVEL = 50

# ─── Badges ────────────────────────────────────────────────────

BADGE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "first_words": {
        "name": "First Words",
        "emoji": "🌟",
        "description": "Hoàn thành bài học đầu tiên",
        "condition": "complete_lesson_count >= 1",
    },
    "on_fire": {
        "name": "On Fire",
        "emoji": "🔥",
        "description": "Trả lời đúng 5 lần liên tiếp",
        "condition": "max_streak >= 5",
    },
    "fast_learner": {
        "name": "Fast Learner",
        "emoji": "⚡",
        "description": "Hoàn thành bài học nhanh",
        "condition": "special",  # do server đánh giá
    },
    "gem_collector": {
        "name": "Gem Collector",
        "emoji": "💎",
        "description": "Thu thập 3 energy gems",
        "condition": "energy_gems >= 3",
    },
    "champion": {
        "name": "Champion",
        "emoji": "🏆",
        "description": "Hoàn thành một module",
        "condition": "completed_modules >= 1",
    },
    "perfect_lesson": {
        "name": "Perfect Lesson",
        "emoji": "💫",
        "description": "Trả lời đúng tất cả câu hỏi trong một bài",
        "condition": "special",  # do server đánh giá
    },
    "explorer": {
        "name": "Explorer",
        "emoji": "🗺️",
        "description": "Khám phá 3 vùng đất",
        "condition": "completed_quest_count >= 3",
    },
    "language_hero": {
        "name": "Language Hero",
        "emoji": "🦸",
        "description": "Hoàn thành toàn bộ hành trình!",
        "condition": "all_quests_completed",
    },
}


@dataclass
class GameProfile:
    """Player game state."""
    total_xp: int = 0
    level: int = 1
    streak: int = 0  # current correct streak
    max_streak: int = 0
    badges: list[str] = field(default_factory=list)  # badge IDs
    completed_quests: list[str] = field(default_factory=list)  # land IDs
    completed_lessons: list[str] = field(default_factory=list)  # lesson IDs
    energy_gems: int = 0
    total_attempts: int = 0
    correct_attempts: int = 0
    current_streak_bonus_active: bool = False

    # ─── XP & Level ─────────────────────────────────────────

    def add_xp(self, amount: int) -> list[str]:
        """Add XP, check level up, return messages."""
        self.total_xp += amount
        messages = []
        new_level = min(self.total_xp // XP_PER_LEVEL + 1, MAX_LEVEL)
        if new_level > self.level:
            old_level = self.level
            self.level = new_level
            messages.append(f"⬆️ Level up! {old_level} → {new_level}")
            if self.level % 5 == 0:
                self.energy_gems += 1
                messages.append(f"💎 Nhận 1 Energy Gem! (Tổng: {self.energy_gems})")
        return messages

    def add_correct_answer(self, is_first_try: bool = False) -> list[str]:
        """Record a correct answer. Returns notification messages."""
        self.total_attempts += 1
        self.correct_attempts += 1
        self.streak += 1
        if self.streak > self.max_streak:
            self.max_streak = self.streak

        messages = []
        xp_gain = XP_CORRECT_ANSWER
        if is_first_try:
            xp_gain += XP_CORRECT_FIRST_TRY
        if self.streak >= STREAK_THRESHOLD:
            xp_gain += XP_STREAK_BONUS

        xp_msgs = self.add_xp(xp_gain)
        messages.append(f"✨ +{xp_gain} XP!")
        messages.extend(xp_msgs)

        return messages

    def add_wrong_answer(self) -> None:
        """Record a wrong answer — reset streak."""
        self.total_attempts += 1
        self.streak = 0

    def complete_lesson(self, lesson_id: str) -> list[str]:
        """Mark a lesson as completed. Returns messages."""
        if lesson_id not in self.completed_lessons:
            self.completed_lessons.append(lesson_id)
        messages = self.add_xp(XP_LESSON_COMPLETE)
        messages.insert(0, f"✅ Bài học hoàn thành! +{XP_LESSON_COMPLETE} XP!")
        return messages

    def complete_quest(self, land_id: str) -> list[str]:
        """Mark a quest/land as completed. Returns messages."""
        if land_id not in self.completed_quests:
            self.completed_quests.append(land_id)
        messages = self.add_xp(XP_QUEST_COMPLETE)
        messages.insert(0, f"🎉 NHIỆM VỤ HOÀN THÀNH! +{XP_QUEST_COMPLETE} XP!")

        # Check for new badges
        new_badges = self._check_new_badges()
        for badge_id in new_badges:
            messages.append(f"{BADGE_DEFINITIONS[badge_id]['emoji']} Badge mới: {BADGE_DEFINITIONS[badge_id]['name']}!")
        return messages

    def award_badge(self, badge_id: str) -> bool:
        """Award a badge if not already owned."""
        if badge_id in self.badges:
            return False
        if badge_id not in BADGE_DEFINITIONS:
            logger.warning(f"Unknown badge: {badge_id}")
            return False
        self.badges.append(badge_id)
        return True

    # ─── Badge checks ────────────────────────────────────────

    def _check_new_badges(self) -> list[str]:
        """Check for newly earned badges."""
        new_badges = []
        checks = {
            "first_words": len(self.completed_lessons) >= 1,
            "on_fire": self.max_streak >= 5,
            "gem_collector": self.energy_gems >= 3,
            "champion": self._completed_module_count() >= 1,
            "explorer": len(self.completed_quests) >= 3,
            "language_hero": len(self.completed_quests) >= 8,
        }
        for badge_id, earned in checks.items():
            if earned and self.award_badge(badge_id):
                new_badges.append(badge_id)
        return new_badges

    def _completed_module_count(self) -> int:
        """Number of completed modules based on quests."""
        return len(self.completed_quests)

    # ─── Serialization ───────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_persist_dict(self) -> dict[str, Any]:
        """Dict for DB storage."""
        d = self.to_dict()
        d["badges"] = json.dumps(d["badges"], ensure_ascii=False)
        d["completed_quests"] = json.dumps(d["completed_quests"], ensure_ascii=False)
        d["completed_lessons"] = json.dumps(d["completed_lessons"], ensure_ascii=False)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameProfile":
        return cls(
            total_xp=int(data.get("total_xp", 0)),
            level=int(data.get("level", 1)),
            streak=int(data.get("streak", 0)),
            max_streak=int(data.get("max_streak", 0)),
            badges=cls._safe_json_list(data.get("badges", "[]")),
            completed_quests=cls._safe_json_list(data.get("completed_quests", "[]")),
            completed_lessons=cls._safe_json_list(data.get("completed_lessons", "[]")),
            energy_gems=int(data.get("energy_gems", 0)),
            total_attempts=int(data.get("total_attempts", 0)),
            correct_attempts=int(data.get("correct_attempts", 0)),
        )

    @staticmethod
    def _safe_json_list(raw: Any) -> list[str]:
        if isinstance(raw, list):
            return [str(x) for x in raw]
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return [str(x) for x in parsed] if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    # ─── Format helpers ──────────────────────────────────────

    def get_progress_summary(self) -> str:
        """Text summary for TTS."""
        badge_names = [BADGE_DEFINITIONS[b]["emoji"] for b in self.badges if b in BADGE_DEFINITIONS]
        badges_str = " ".join(badge_names) if badge_names else "(chưa có)"
        accuracy = (self.correct_attempts / max(self.total_attempts, 1)) * 100
        return (
            f"Cấp độ {self.level}, {self.total_xp} XP. "
            f"Huy hiệu: {badges_str}. "
            f"Độ chính xác: {accuracy:.0f}%. "
            f"Streak hiện tại: {self.streak}."
        )
