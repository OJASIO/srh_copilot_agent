"""SRH AI Copilot API (Multi-Channel Interaction Layer entry point).

Run:  uvicorn main:app --reload
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config.settings import settings
from core.auth import Principal, create_token, current_user, rate_limit_key, require_api_key, require_roles
from core.logging_config import setup_logging
from core.orchestrator import Orchestrator
from core.registry import AgentRegistry
from core.schemas import AgentInfo, AgentRequest, Attachment, ChatRequest, ChatResponse, validate_inputs
from core.services import ServiceContainer

setup_logging()
log = logging.getLogger("api")
limiter = Limiter(key_func=rate_limit_key, default_limits=[settings.rate_limit])


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.is_prod:
        settings.assert_production_safe()
    services = ServiceContainer.build(settings)
    await services.startup()
    registry = AgentRegistry(services)
    registry.load()
    if registry.load_errors:
        log.error("agent load errors: %s", registry.load_errors)
    app.state.services = services
    app.state.registry = registry
    app.state.orchestrator = Orchestrator(registry, services)
    log.info("plugged agents: %s", list(registry.agents))
    yield


class BodySizeLimit:
    """Reject request bodies above `max_bytes` while they arrive, before FastAPI
    parses a multipart form into memory or a temporary file. Pure ASGI, so it
    also covers chunked uploads without a Content-Length header."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.max_bytes:
            return await self._reject(send)
        received, started = 0, False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(send)

    async def _reject(self, send):
        body = json.dumps({"detail": f"request body exceeds {settings.max_upload_mb} MB"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    pass


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan,
              docs_url=None if settings.is_prod else "/docs")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
# upload limit plus room for the form fields (message, job description)
app.add_middleware(BodySizeLimit, max_bytes=(settings.max_upload_mb + 1) * 1024 * 1024)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# Public

@app.get("/health")
async def health(request: Request):
    reg: AgentRegistry = request.app.state.registry
    return {"status": "ok", "env": settings.app_env, "llm": settings.llm_provider,
            "vector_backend": settings.vector_backend, "agents": list(reg.agents),
            "load_errors": reg.load_errors}


# Auth (demo issuer; replace by SSO in production, see core/auth.py)

_DEMO_USERS = {"student": ("student", ["student"]), "staff": ("staff", ["staff", "student"]),
               "hr": ("hr", ["hr", "staff"]), "admin": ("admin", ["admin", "staff", "student"])}


@app.post("/auth/token", dependencies=[Depends(require_api_key)])
async def issue_token(username: str = Form(...)):
    if settings.is_prod:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if username not in _DEMO_USERS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown demo user")
    uid, roles = _DEMO_USERS[username]
    return {"access_token": create_token(uid, roles, name=username.title()), "token_type": "bearer"}


# Agents

@app.get("/agents", response_model=list[AgentInfo], dependencies=[Depends(require_api_key)])
async def list_agents(request: Request):
    return request.app.state.registry.infos()


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
@limiter.limit(settings.rate_limit)
async def chat(request: Request, body: ChatRequest, user: Principal = Depends(current_user)):
    return await _run(request, user, body.message, body.session_id, body.agent_id, body.task_id, body.language,
                      body.inputs, [])


@app.post("/chat/upload", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
@limiter.limit(settings.rate_limit)
async def chat_with_file(request: Request, user: Principal = Depends(current_user),
                         message: str = Form("Please review the attached document."),
                         session_id: str | None = Form(None, max_length=64), agent_id: str | None = Form(None),
                         task_id: str | None = Form(None), language: str = Form("auto"),
                         inputs: str = Form("{}", description='JSON object of task fields, e.g. {"job_description": "..."}'),
                         file: UploadFile = File(...)):
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"file exceeds {settings.max_upload_mb} MB")
    try:
        parsed = json.loads(inputs or "{}")
        if not isinstance(parsed, dict) or not all(isinstance(v, str) for v in parsed.values()):
            raise ValueError("inputs must be a JSON object of strings")
        parsed = validate_inputs(parsed)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise HTTPException(422, f"invalid inputs: {exc}") from exc
    att = Attachment(filename=file.filename or "upload", content_type=file.content_type or "application/octet-stream",
                     data=data)
    return await _run(request, user, message, session_id, agent_id, task_id, language, parsed, [att])


async def _run(request, user, message, session_id, agent_id, task_id, language, inputs, attachments) -> ChatResponse:
    orch: Orchestrator = request.app.state.orchestrator
    req = AgentRequest(session_id=session_id or uuid4().hex, user_id=user.user_id, agent_id=agent_id,
                       task_id=task_id, message=message, attachments=attachments, language=language,
                       inputs=inputs, metadata={"roles": user.roles, "name": user.name})
    try:
        resp = await orch.run(req)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return ChatResponse(session_id=req.session_id, agent_id=resp.agent_id, task_id=resp.task_id,
                        content=resp.content, citations=resp.citations, structured=resp.structured, trace=resp.trace)


# Admin: hot plug / unplug without restart

@app.get("/admin/agents", dependencies=[Depends(require_api_key)])
async def admin_agents(request: Request, user: Principal = Depends(require_roles("admin"))):
    reg: AgentRegistry = request.app.state.registry
    reg.discover()
    return {"enabled": list(reg.agents), "available": list(reg.available), "errors": reg.load_errors}


@app.post("/admin/agents/{agent_id}/plug", dependencies=[Depends(require_api_key)])
async def admin_plug(agent_id: str, request: Request, user: Principal = Depends(require_roles("admin"))):
    reg: AgentRegistry = request.app.state.registry
    try:
        agent = reg.plug(agent_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no manifest for {agent_id}")
    return await agent.healthcheck()


@app.post("/admin/agents/{agent_id}/unplug", dependencies=[Depends(require_api_key)])
async def admin_unplug(agent_id: str, request: Request, user: Principal = Depends(require_roles("admin"))):
    reg: AgentRegistry = request.app.state.registry
    if not reg.unplug(agent_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{agent_id} is not plugged")
    return {"unplugged": agent_id, "enabled": list(reg.agents)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.api_host, port=settings.api_port, reload=not settings.is_prod)
