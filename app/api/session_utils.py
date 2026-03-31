from fastapi.responses import Response

def set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key="nexus_session",
        value=token,
        httponly=True,
        max_age=86400,
        samesite="none",
        secure=True,
        domain=".tanlinh.dev",
        path="/",
    )

