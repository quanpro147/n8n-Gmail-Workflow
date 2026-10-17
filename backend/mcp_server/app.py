"""ASGI app: the MCP Streamable HTTP endpoint behind a shared bearer token."""

import hmac
import re

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.logging import correlation_id

MCP_PATH = "/mcp"
# Printable ASCII without spaces, bounded like a Gmail message_id; anything else is ignored.
_CORRELATION_ID = re.compile(r"[!-~]{1,255}")
LOCAL_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")


class BearerTokenMiddleware:
    """Rejects HTTP requests without `Authorization: Bearer <token>` (constant-time compare)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            provided = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(provided, self._expected):
                response = JSONResponse(
                    {"error": {"code": "UNAUTHORIZED", "message": "Invalid or missing token."}},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


class CorrelationIdMiddleware:
    """Tags the request's log lines with the caller's X-Correlation-ID (if well-formed)."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        raw = dict(scope["headers"]).get(b"x-correlation-id", b"").decode("latin-1")
        token = correlation_id.set(raw if _CORRELATION_ID.fullmatch(raw) else None)
        try:
            await self._app(scope, receive, send)
        finally:
            correlation_id.reset(token)


def read_only_engine(database_url: str) -> Engine:
    # Every transaction is READ ONLY, so a bug in a tool can't write, whatever SQL it runs.
    return create_engine(
        database_url, pool_pre_ping=True, execution_options={"postgresql_readonly": True}
    )


def read_only_sessionmaker(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=read_only_engine(database_url), expire_on_commit=False)


def transport_security(extra_allowed_hosts: tuple[str, ...] = ()) -> TransportSecuritySettings:
    """The SDK's localhost DNS-rebinding defaults, plus named hosts such as the compose
    service name (`mcp:*`) that the backend container uses to reach this server."""
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*LOCAL_HOSTS, *extra_allowed_hosts],
        allowed_origins=[f"http://{host}" for host in LOCAL_HOSTS],
    )


def build_app(
    server: MCPServer, *, token: str, extra_allowed_hosts: tuple[str, ...] = ()
) -> ASGIApp:
    # Stateless + JSON: each POST is self-contained, nothing to resume, no session state.
    starlette_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(extra_allowed_hosts),
    )
    return BearerTokenMiddleware(CorrelationIdMiddleware(starlette_app), token)
