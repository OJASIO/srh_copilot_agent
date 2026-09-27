"""Authentication (orchestration layer).

Two layers, both optional to combine:
  1. API key   the frontend (or any client) sends X-API-Key. Stops anonymous
               internet traffic from hitting the LLM.
  2. JWT       identifies the person (student / staff) and their role. Issued by
               /auth/token today (demo users); in production replaced by the
               university's SSO (Microsoft Entra ID via OIDC) issuing the same claims.

Agents receive `user_id` and `roles` through AgentRequest.metadata and can
restrict tasks (e.g. HR Compliance only for role "hr").
"""

from __future__ import annotations

import hmac
import re
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from config.settings import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer = HTTPBearer(auto_error=False)


class Principal(BaseModel):
    user_id: str
    roles: list[str] = []
    name: str = ""


def require_api_key(key: str | None = Security(api_key_header)) -> None:
    if not key or not hmac.compare_digest(key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")


def create_token(user_id: str, roles: list[str], name: str = "") -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "roles": roles, "name": name, "iat": now,
               "exp": now + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def current_user(creds: HTTPAuthorizationCredentials | None = Security(bearer)) -> Principal:
    if creds is None:
        if settings.is_prod:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        return Principal(user_id="anonymous", roles=["student"], name="Anonymous")
    try:
        data = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
    return Principal(user_id=data["sub"], roles=data.get("roles", []), name=data.get("name", ""))


def rate_limit_key(request: Request) -> str:
    """Who a request counts against for rate limiting.

    Behind the Streamlit UI every request comes from the same loopback address, so
    limiting by IP would make one global limit for all users. Order: the signed-in
    user (valid JWT), then the browser session id the UI sends as X-Client-Id, then
    the IP address."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            sub = jwt.decode(auth[7:], settings.jwt_secret, algorithms=[settings.jwt_algorithm]).get("sub")
            if sub:
                return f"user:{sub}"
        except jwt.PyJWTError:
            pass
    client = request.headers.get("x-client-id", "")
    if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", client):
        return f"client:{client}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def require_roles(*roles: str):
    def dep(user: Principal = Depends(current_user)) -> Principal:
        if roles and not set(roles) & set(user.roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return user
    return dep
