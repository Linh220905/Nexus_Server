from datetime import datetime
from datetime import timedelta
from typing import Optional
import secrets
import sqlite3
from ..database.connection import get_db_connection
from .models import UserCreate, UserUpdate, UserInDB
from .security import get_password_hash, verify_password


def get_user_by_username(username: str) -> Optional[UserInDB]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash, role, created_at, updated_at FROM users WHERE username = ?",
            (username,)
        )
        row = cursor.fetchone()
        
        if row:
            return UserInDB(
                id=row['id'],
                username=row['username'],
                password_hash=row['password_hash'],
                role=row['role'],
                created_at=row['created_at'],
                updated_at=row['updated_at']
            )
        return None


def create_user(user: UserCreate) -> Optional[UserInDB]:
    """Create a new user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        password_hash = get_password_hash(user.password)
        try:
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, role, created_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (user.username, password_hash, user.role.value)
            )
            conn.commit()
            
            # Return the created user
            return get_user_by_username(user.username)
        except sqlite3.IntegrityError:
            raise ValueError(f"Username '{user.username}' already exists")


def update_user(username: str, user_update: UserUpdate) -> Optional[UserInDB]:
    """Update user information."""
    db_user = get_user_by_username(username)
    if not db_user:
        return None
    
    # Prepare update fields
    updates = []
    params = []
    
    if user_update.password:
        password_hash = get_password_hash(user_update.password)
        updates.append("password_hash = ?")
        params.append(password_hash)
    
    if user_update.role:
        updates.append("role = ?")
        params.append(user_update.role.value)
    
    # Add updated_at timestamp
    updates.append("updated_at = CURRENT_TIMESTAMP")
    
    # Add username to the end of params for WHERE clause
    params.append(username)
    
    if updates:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = f"UPDATE users SET {', '.join(updates)} WHERE username = ?"
            cursor.execute(query, params)
            conn.commit()
    
    return get_user_by_username(username)


def authenticate_user(username: str, password: str) -> Optional[UserInDB]:
    """Authenticate a user by username and password."""
    user = get_user_by_username(username)
    if not user:
        return None
    
    if verify_password(password, user.password_hash):
        return user
    
    return None


def upsert_oauth_user(
    username: str,
    provider: str,
    provider_user_id: Optional[str] = None,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    default_role: str = "viewer",
) -> UserInDB:
    """Create or update OAuth user and return the current DB row."""
    normalized_username = (username or "").strip().lower()
    if not normalized_username:
        raise ValueError("username is required")

    normalized_provider = (provider or "local").strip().lower()
    normalized_provider_user_id = (provider_user_id or "").strip() or f"email:{normalized_username}"

    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT username FROM users WHERE username = ?", (normalized_username,))
        row_by_username = cursor.fetchone()

        cursor.execute(
            "SELECT username FROM users WHERE auth_provider = ? AND provider_user_id = ?",
            (normalized_provider, normalized_provider_user_id),
        )
        row_by_provider_id = cursor.fetchone()

        target_username = None
        if row_by_username:
            target_username = row_by_username["username"]
        elif row_by_provider_id:
            target_username = row_by_provider_id["username"]

        if target_username:
            # Do not rewrite username on provider-id match to avoid accidental account overwrite.
            provider_id_belongs_to_target = (
                not row_by_provider_id or row_by_provider_id["username"] == target_username
            )

            if provider_id_belongs_to_target:
                cursor.execute(
                    """
                    UPDATE users
                    SET auth_provider = ?,
                        provider_user_id = ?,
                        display_name = ?,
                        avatar_url = ?,
                        last_login_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE username = ?
                    """,
                    (
                        normalized_provider,
                        normalized_provider_user_id,
                        display_name,
                        avatar_url,
                        target_username,
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE users
                    SET auth_provider = ?,
                        display_name = ?,
                        avatar_url = ?,
                        last_login_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE username = ?
                    """,
                    (
                        normalized_provider,
                        display_name,
                        avatar_url,
                        target_username,
                    ),
                )
        else:
            random_password_hash = get_password_hash(secrets.token_urlsafe(32))
            cursor.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, auth_provider,
                    provider_user_id, display_name, avatar_url,
                    last_login_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    normalized_username,
                    random_password_hash,
                    default_role,
                    normalized_provider,
                    normalized_provider_user_id,
                    display_name,
                    avatar_url,
                ),
            )
            target_username = normalized_username

        conn.commit()

    user = get_user_by_username(target_username or normalized_username)
    if not user:
        raise ValueError("Failed to upsert OAuth user")
    return user


def list_users_for_admin(search: str = "", provider: Optional[str] = None) -> list[dict]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT id, username, role, auth_provider, provider_user_id, display_name,
                   avatar_url, last_login_at, created_at, updated_at
            FROM users
            WHERE 1=1
        """
        params: list[object] = []

        if search:
            query += " AND (username LIKE ? OR COALESCE(display_name, '') LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like])

        if provider and provider != "all":
            query += " AND auth_provider = ?"
            params.append(provider)

        query += " ORDER BY datetime(updated_at) DESC, id DESC"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        return [
            {
                "id": row["id"],
                "username": row["username"],
                "role": row["role"],
                "auth_provider": row["auth_provider"] or "local",
                "provider_user_id": row["provider_user_id"],
                "display_name": row["display_name"],
                "avatar_url": row["avatar_url"],
                "last_login_at": row["last_login_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]


def update_user_role_for_admin(username: str, role: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE username = ?",
            (role, username),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_user_for_admin(username: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        return cursor.rowcount > 0


def get_user_registration_stats(days: int = 14, provider: Optional[str] = None) -> list[dict]:
    """Return daily registration counts for the last N days (inclusive)."""
    safe_days = max(1, min(days, 180))
    start_date = datetime.utcnow().date() - timedelta(days=safe_days - 1)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT date(created_at) AS day, COUNT(*) AS count
            FROM users
            WHERE date(created_at) >= date(?)
        """
        params: list[object] = [start_date.isoformat()]

        if provider and provider != "all":
            query += " AND auth_provider = ?"
            params.append(provider)

        query += " GROUP BY date(created_at) ORDER BY day ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()

    counts_by_day = {
        row["day"]: int(row["count"])
        for row in rows
        if row["day"]
    }

    result: list[dict] = []
    for i in range(safe_days):
        day = start_date + timedelta(days=i)
        day_key = day.isoformat()
        result.append({"date": day_key, "count": counts_by_day.get(day_key, 0)})
    return result