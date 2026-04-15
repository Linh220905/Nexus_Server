from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from authlib.integrations.starlette_client import OAuth
import os
from dotenv import load_dotenv
import jwt
from datetime import datetime, timedelta, timezone
from app.api.session_utils import set_auth_cookie, clear_auth_cookie
from app.auth.crud import upsert_oauth_user
router = APIRouter(prefix="/auth", tags=["Authentication"])

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY không được để trống! Hãy kiểm tra file .env.")

oauth = OAuth()
oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)


def create_session_token(email: str, role: str = "viewer") -> str:
    payload = {
        "email": email,
        "sub": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_session_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])


def get_session_payload(request: Request) -> dict:
    session_token = request.cookies.get("nexus_session")
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return decode_session_token(session_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session")


def require_admin(request: Request) -> dict:
    payload = get_session_payload(request)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload


def require_viewer(request: Request) -> dict:
    return get_session_payload(request)


@router.get("/google-login")
async def google_login(request: Request):
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI") or str(request.url_for("google_auth"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google-auth")
async def google_auth(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user = token.get("userinfo")

    if not user:
        raise HTTPException(status_code=400, detail="Google login failed")

    email = user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email")

    db_user = upsert_oauth_user(
        username=email,
        provider="google",
        provider_user_id=user.get("sub") or user.get("id"),
        display_name=user.get("name"),
        avatar_url=user.get("picture"),
        default_role="viewer",
    )

    session_token = create_session_token(
        email,
        role=getattr(db_user, "role", "viewer"),
    )

    frontend_redirect = os.getenv("FRONTEND_REDIRECT_URI") or str(request.base_url) + "dashboard/"
    response = RedirectResponse(url=frontend_redirect, status_code=302)

    set_auth_cookie(response, session_token)

    # Xóa session_backend cookie sau khi OAuth hoàn tất
    # Tránh cookie cũ (secure/samesite khác) gây xung đột
    response.delete_cookie(
        key="session_backend",
        path="/",
        domain=".tanlinh.dev",
    )

    return response

@router.get("/me")
async def get_current_user(request: Request):
    payload = get_session_payload(request)
    return {"email": payload["email"], "role": payload.get("role", "viewer")}


@router.api_route("/logout", methods=["GET", "POST"])
async def logout():
    response = RedirectResponse(url="/", status_code=302)
    clear_auth_cookie(response)
    # Xóa session_backend (OAuth temp cookie)
    response.delete_cookie(key="session_backend", path="/", domain=".tanlinh.dev")
    response.delete_cookie(key="session_backend", path="/")
    return response
