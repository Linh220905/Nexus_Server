"""
CRUD for lesson_progress table.
"""
from __future__ import annotations

import json
from typing import Any

from app.database.connection import get_db_connection


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def save_lesson_progress(robot_mac: str, context: dict[str, str | None]) -> None:
    """Upsert lesson progress for a robot."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM lesson_progress WHERE robot_mac = ?", (robot_mac,))
        existing = cur.fetchone()

        teaching_topic_id = context.get("teaching_topic_id") or ""
        player_name = (context.get("player_name") or "").strip()
        intro_done = "1" if str(context.get("intro_done") or "0") == "1" else "0"
        onboarding_state = (context.get("onboarding_state") or "").strip()

        if existing:
            cur.execute(
                """UPDATE lesson_progress
                   SET current_lesson_id = ?, teaching_topic_id = ?,
                       player_name = ?, intro_done = ?, onboarding_state = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE robot_mac = ?""",
                (
                    _safe_int(context.get("current_lesson_id", 0)),
                    teaching_topic_id,
                    player_name, intro_done, onboarding_state,
                    robot_mac,
                ),
            )
        else:
            cur.execute(
                """INSERT INTO lesson_progress
                   (robot_mac, current_lesson_id, teaching_topic_id,
                    player_name, intro_done, onboarding_state)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    robot_mac,
                    _safe_int(context.get("current_lesson_id", 0)),
                    teaching_topic_id,
                    player_name, intro_done, onboarding_state,
                ),
            )
        conn.commit()


def load_lesson_progress(robot_mac: str) -> dict[str, Any] | None:
    """Load lesson progress for a robot. Returns None if not found."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT module_index, lesson_index, step_index,
                      teaching_topic_id, lesson_plan, completed_lessons,
                      interaction_mode, current_lesson_id, player_name,
                      intro_done, onboarding_state
               FROM lesson_progress
               WHERE robot_mac = ?""",
            (robot_mac,),
        )
        row = cur.fetchone()
        if not row:
            return None

        return {
            "current_lesson_id": str(row["current_lesson_id"] or 0),
            "teaching_topic_id": str(row["teaching_topic_id"] or ""),
            "completed_lessons": str(row["completed_lessons"] or "[]"),
            "player_name": str(row["player_name"] or ""),
            "intro_done": "1" if str(row["intro_done"] or "0") == "1" else "0",
            "onboarding_state": str(row["onboarding_state"] or ""),
            "state": "ACTIVE",
            "word_index": "0",
        }


def mark_lesson_completed(robot_mac: str, lesson_id: str) -> list[str]:
    """Mark a lesson as completed. Returns updated completed_lessons list."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT completed_lessons FROM lesson_progress WHERE robot_mac = ?",
            (robot_mac,),
        )
        row = cur.fetchone()
        completed = []
        if row:
            try:
                completed = json.loads(row["completed_lessons"])
            except (json.JSONDecodeError, TypeError):
                completed = []

        if lesson_id not in completed:
            completed.append(lesson_id)

        cur.execute(
            "UPDATE lesson_progress SET completed_lessons = ?, updated_at = CURRENT_TIMESTAMP WHERE robot_mac = ?",
            (json.dumps(completed, ensure_ascii=False), robot_mac),
        )
        conn.commit()
    return completed


def delete_lesson_progress(robot_mac: str) -> None:
    """Delete lesson progress for a robot."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM lesson_progress WHERE robot_mac = ?", (robot_mac,))
        conn.commit()
