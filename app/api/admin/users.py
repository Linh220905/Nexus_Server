from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth_google import require_admin
from app.auth.crud import (
    delete_user_for_admin,
    get_user_registration_stats,
    list_users_for_admin,
    update_user_role_for_admin,
)

router = APIRouter(prefix="/admin", tags=["Admin Users"])

ALLOWED_ROLES = {"admin", "viewer", "user"}


class UpdateUserRoleRequest(BaseModel):
    role: str


@router.get("/users")
async def list_users(
    q: str = Query("", max_length=100),
    provider: str = Query("all", max_length=32),
    session: dict = Depends(require_admin),
):
    _ = session
    return {"ok": True, "items": list_users_for_admin(search=q.strip(), provider=provider.strip())}


@router.get("/users/stats/registrations")
async def registration_stats(
    days: int = Query(14, ge=1, le=180),
    provider: str = Query("all", max_length=32),
    session: dict = Depends(require_admin),
):
    _ = session
    normalized_provider = provider.strip().lower()
    return {
        "ok": True,
        "days": days,
        "provider": normalized_provider,
        "items": get_user_registration_stats(days=days, provider=normalized_provider),
    }


@router.patch("/users/{username}/role")
async def update_user_role(
    username: str,
    body: UpdateUserRoleRequest,
    session: dict = Depends(require_admin),
):
    role = (body.role or "").strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")

    current_admin = session.get("email", "")
    if current_admin == username and role != "admin":
        raise HTTPException(status_code=400, detail="You cannot remove your own admin role")

    if not update_user_role_for_admin(username=username, role=role):
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True, "message": "Role updated"}


@router.delete("/users/{username}")
async def delete_user(
    username: str,
    session: dict = Depends(require_admin),
):
    current_admin = session.get("email", "")
    if current_admin == username:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    if not delete_user_for_admin(username=username):
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True, "message": "User deleted"}
