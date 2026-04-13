"""CRUD for parent assignments/custom tasks."""

from __future__ import annotations

from datetime import datetime

from app.database.connection import get_db_connection


def list_assignments_for_user(username: str) -> list[dict]:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, owner_username, title, instructions, category, difficulty,
                      is_active, due_at, created_at, updated_at
               FROM assignments
               WHERE owner_username = ?
               ORDER BY is_active DESC, updated_at DESC""",
            (username,),
        )
        rows = cur.fetchall()
        return [
            {
                "id": row["id"],
                "owner_username": row["owner_username"],
                "title": row["title"],
                "instructions": row["instructions"],
                "category": row["category"],
                "difficulty": row["difficulty"],
                "is_active": bool(row["is_active"]),
                "due_at": row["due_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]


def create_assignment_for_user(username: str, payload: dict) -> dict:
    title = str(payload.get("title") or "").strip()
    instructions = str(payload.get("instructions") or "").strip()
    if not title or not instructions:
        raise ValueError("title và instructions là bắt buộc")

    category = str(payload.get("category") or "custom").strip() or "custom"
    difficulty = str(payload.get("difficulty") or "beginner").strip() or "beginner"
    is_active = 1 if payload.get("is_active", True) else 0
    due_at = payload.get("due_at")
    due_at = str(due_at).strip() if due_at else None

    now = datetime.utcnow().isoformat()
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO assignments
               (owner_username, title, instructions, category, difficulty, is_active, due_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (username, title, instructions, category, difficulty, is_active, due_at, now, now),
        )
        assignment_id = cur.lastrowid
        if assignment_id is None:
            raise RuntimeError("không thể tạo assignment")
        conn.commit()

    return get_assignment_by_id(username, int(assignment_id))


def get_assignment_by_id(username: str, assignment_id: int) -> dict:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, owner_username, title, instructions, category, difficulty,
                      is_active, due_at, created_at, updated_at
               FROM assignments WHERE id = ? AND owner_username = ?""",
            (assignment_id, username),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("assignment không tồn tại")
        return {
            "id": row["id"],
            "owner_username": row["owner_username"],
            "title": row["title"],
            "instructions": row["instructions"],
            "category": row["category"],
            "difficulty": row["difficulty"],
            "is_active": bool(row["is_active"]),
            "due_at": row["due_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def update_assignment_for_user(username: str, assignment_id: int, payload: dict) -> dict:
    current = get_assignment_by_id(username, assignment_id)

    title = str(payload.get("title", current["title"]))
    instructions = str(payload.get("instructions", current["instructions"]))
    category = str(payload.get("category", current["category"]))
    difficulty = str(payload.get("difficulty", current["difficulty"]))
    is_active = payload.get("is_active", current["is_active"])
    due_at = payload.get("due_at", current["due_at"])

    now = datetime.utcnow().isoformat()
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE assignments
               SET title = ?, instructions = ?, category = ?, difficulty = ?,
                   is_active = ?, due_at = ?, updated_at = ?
               WHERE id = ? AND owner_username = ?""",
            (
                title.strip(),
                instructions.strip(),
                category.strip() or "custom",
                difficulty.strip() or "beginner",
                1 if is_active else 0,
                str(due_at).strip() if due_at else None,
                now,
                assignment_id,
                username,
            ),
        )
        conn.commit()

    return get_assignment_by_id(username, assignment_id)


def delete_assignment_for_user(username: str, assignment_id: int) -> None:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM assignments WHERE id = ? AND owner_username = ?", (assignment_id, username))
        conn.commit()


def get_latest_active_assignment_for_robot(robot_mac: str) -> dict | None:
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT a.id, a.title, a.instructions, a.category, a.difficulty, a.due_at,
                      a.created_at, a.updated_at, r.owner_username
               FROM assignments a
               INNER JOIN robots r ON r.owner_username = a.owner_username
               WHERE r.mac_address = ? AND a.is_active = 1
               ORDER BY a.updated_at DESC
               LIMIT 1""",
            (robot_mac,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "instructions": row["instructions"],
            "category": row["category"],
            "difficulty": row["difficulty"],
            "due_at": row["due_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "owner_username": row["owner_username"],
        }
