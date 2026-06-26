"""
CRUD for game_profiles table.
"""
from __future__ import annotations

import json
from typing import Any

from app.database.connection import get_db_connection
from app.services.game_profile import GameProfile


def save_game_profile(robot_mac: str, profile: GameProfile) -> None:
    """Upsert game profile for a robot."""
    data = profile.to_persist_dict()

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO game_profiles
               (robot_mac, total_xp, level, streak, max_streak,
                badges, completed_quests, completed_lessons,
                energy_gems, total_attempts, correct_attempts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(robot_mac) DO UPDATE SET
               total_xp = excluded.total_xp,
               level = excluded.level,
               streak = excluded.streak,
               max_streak = excluded.max_streak,
               badges = excluded.badges,
               completed_quests = excluded.completed_quests,
               completed_lessons = excluded.completed_lessons,
               energy_gems = excluded.energy_gems,
               total_attempts = excluded.total_attempts,
               correct_attempts = excluded.correct_attempts,
               updated_at = CURRENT_TIMESTAMP""",
            (
                robot_mac,
                data["total_xp"], data["level"],
                data["streak"], data["max_streak"],
                data["badges"], data["completed_quests"],
                data["completed_lessons"],
                data["energy_gems"],
                data["total_attempts"], data["correct_attempts"],
            ),
        )
        conn.commit()


def load_game_profile(robot_mac: str) -> GameProfile | None:
    """Load game profile for a robot. Returns None if not found."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT total_xp, level, streak, max_streak,
                      badges, completed_quests, completed_lessons,
                      energy_gems, total_attempts, correct_attempts
               FROM game_profiles
               WHERE robot_mac = ?""",
            (robot_mac,),
        )
        row = cur.fetchone()
        if not row:
            return None

        return GameProfile.from_dict({
            "total_xp": row["total_xp"],
            "level": row["level"],
            "streak": row["streak"],
            "max_streak": row["max_streak"],
            "badges": row["badges"],
            "completed_quests": row["completed_quests"],
            "completed_lessons": row["completed_lessons"],
            "energy_gems": row["energy_gems"],
            "total_attempts": row["total_attempts"],
            "correct_attempts": row["correct_attempts"],
        })


def delete_game_profile(robot_mac: str) -> None:
    """Delete game profile for a robot."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM game_profiles WHERE robot_mac = ?", (robot_mac,))
        conn.commit()
