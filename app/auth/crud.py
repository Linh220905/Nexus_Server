from datetime import datetime
from typing import Optional
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