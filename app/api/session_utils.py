from fastapi import Request
from fastapi.responses import Response


def _is_local_request(request: Request) -> bool:
    host = request.headers.get("host", "").split(":")[0].lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def set_auth_cookie(response: Response, token: str, request: Request):
    cookie_kwargs = {
        "key": "nexus_session",
        "value": token,
        "httponly": True,
        "max_age": 86400,
        "path": "/",
    }

    if _is_local_request(request):
        # Local HTTP dev: Secure=False and no domain so browser accepts cookie.
        cookie_kwargs.update({"samesite": "lax", "secure": False})
    else:
        # Production HTTPS domain cookie.
        cookie_kwargs.update(
            {
                "samesite": "none",
                "secure": True,
                "domain": ".tanlinh.dev",
            }
        )

    response.set_cookie(**cookie_kwargs)

