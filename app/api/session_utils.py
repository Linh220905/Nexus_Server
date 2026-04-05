from fastapi.responses import Response
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def set_auth_cookie(response: Response, token: str):
    cookie_domain = (os.getenv("COOKIE_DOMAIN", "").strip() or None)
    cookie_secure = _env_bool("COOKIE_SECURE", False)
    cookie_samesite = os.getenv("COOKIE_SAMESITE", "none" if cookie_secure else "lax").strip().lower()
    if cookie_samesite not in {"lax", "strict", "none"}:
        cookie_samesite = "none" if cookie_secure else "lax"

    cookie_kwargs = {
        "key": "nexus_session",
        "value": token,
        "httponly": True,
        "max_age": 86400,
        "samesite": cookie_samesite,
        "secure": cookie_secure,
        "path": "/",
    }
    if cookie_domain:
        cookie_kwargs["domain"] = cookie_domain

    response.set_cookie(
        **cookie_kwargs,
    )


def clear_auth_cookie(response: Response):
    cookie_domain = (os.getenv("COOKIE_DOMAIN", "").strip() or None)
    cookie_secure = _env_bool("COOKIE_SECURE", False)
    cookie_samesite = os.getenv("COOKIE_SAMESITE", "none" if cookie_secure else "lax").strip().lower()
    if cookie_samesite not in {"lax", "strict", "none"}:
        cookie_samesite = "none" if cookie_secure else "lax"

    delete_kwargs = {
        "key": "nexus_session",
        "path": "/",
        "secure": cookie_secure,
        "httponly": True,
        "samesite": cookie_samesite,
    }

    response.delete_cookie(**delete_kwargs)
    if cookie_domain:
        response.delete_cookie(**{**delete_kwargs, "domain": cookie_domain})

