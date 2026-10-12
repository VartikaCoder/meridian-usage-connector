"""HTTP layer: maps requests to AdminService / ConnectorTools and results to responses.

No decisions are made here. Identity is simulated: the caller is the directory user named
in the `X-Meridian-User` header (user id or email). Denials are HTTP 403 with the contract's
Denial body; malformed input is HTTP 400 (or 422 from request validation).

Run: MERIDIAN_ENV=dev uv run uvicorn --factory meridian_usage.app:create_app --reload
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool

from meridian_usage.admin import AdminService, Result
from meridian_usage.authz import Denial
from meridian_usage.directory import User
from meridian_usage.settings import Settings
from meridian_usage.tools import TOOL_NAMES, InvalidArgument

log = logging.getLogger("meridian_usage")

STATIC = Path(__file__).parent / "static"

CallerHeader = Annotated[
    str | None,
    Header(alias="X-Meridian-User", description="Simulated identity: directory user id or email"),
]


class SetCapabilityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    svc = AdminService.from_settings(settings)
    log.info(
        "env=%s records=%d rejected=%d warnings=%d",
        settings.environment,
        len(svc.ingest.records),
        len(svc.ingest.rejected),
        len(svc.ingest.warnings),
    )

    app = FastAPI(
        title="Meridian usage and admin service",
        version="0.1.0",
        description=f"Environment: {settings.environment}. Identity is simulated.",
    )
    app.state.service = svc

    @app.exception_handler(InvalidArgument)
    async def invalid_argument(_: Request, exc: InvalidArgument) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"error": "invalid_argument", "reason": str(exc)}
        )

    def respond(result: Result | Denial) -> Any:
        if isinstance(result, Denial):
            return JSONResponse(status_code=403, content=result.to_dict())
        return result

    def caller_or_denial(header: str | None) -> User | Denial:
        return svc.resolve_caller(header)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def admin_page() -> HTMLResponse:
        return HTMLResponse((STATIC / "index.html").read_text())

    @app.get("/api/directory/users", summary="Users for the page's identity switcher")
    def directory_users() -> list[dict[str, Any]]:
        return svc.directory_users()

    @app.get("/api/me", response_model=None)
    def me(x_meridian_user: CallerHeader = None) -> Any:
        c = caller_or_denial(x_meridian_user)
        return respond(c if isinstance(c, Denial) else svc.me(c))

    @app.get("/api/usage/by-group", response_model=None, summary="Usage by group and platform")
    def usage_by_group(
        period: Annotated[str, Query(examples=["2026-09", "2026-W36", "last_30_days"])],
        x_meridian_user: CallerHeader = None,
    ) -> Any:
        c = caller_or_denial(x_meridian_user)
        return respond(c if isinstance(c, Denial) else svc.usage_by_group(c, period))

    @app.get("/api/usage/weekly", response_model=None, summary="Usage by week, group, platform")
    def weekly(group: str | None = None, x_meridian_user: CallerHeader = None) -> Any:
        c = caller_or_denial(x_meridian_user)
        return respond(c if isinstance(c, Denial) else svc.weekly(c, group))

    @app.get("/api/capabilities", response_model=None)
    def capabilities(group: str | None = None, x_meridian_user: CallerHeader = None) -> Any:
        c = caller_or_denial(x_meridian_user)
        return respond(c if isinstance(c, Denial) else svc.capabilities(c, group))

    @app.put("/api/capabilities/{group}/{capability}", response_model=None)
    def set_capability(
        group: str,
        capability: str,
        body: SetCapabilityBody,
        x_meridian_user: CallerHeader = None,
    ) -> Any:
        c = caller_or_denial(x_meridian_user)
        if isinstance(c, Denial):
            return respond(c)
        return respond(svc.set_capability(c, group, capability, body.enabled))

    @app.get("/api/capabilities/audit", response_model=None)
    def audit(group: str | None = None, x_meridian_user: CallerHeader = None) -> Any:
        c = caller_or_denial(x_meridian_user)
        return respond(c if isinstance(c, Denial) else svc.audit(c, group))

    @app.get("/api/ingest/report", response_model=None)
    def ingest_report(x_meridian_user: CallerHeader = None) -> Any:
        c = caller_or_denial(x_meridian_user)
        return respond(c if isinstance(c, Denial) else svc.ingest_report(c))

    @app.post(
        "/api/connector/tools/{tool}",
        response_model=None,
        summary="Connector tool call (used by the MCP server); applies the connector gate",
    )
    def connector_tool(
        tool: str,
        args: Annotated[dict[str, Any] | None, Body()] = None,
        x_meridian_user: CallerHeader = None,
    ) -> Any:
        if tool not in TOOL_NAMES:
            raise InvalidArgument(f"unknown tool {tool!r}")
        c = caller_or_denial(x_meridian_user)
        if isinstance(c, Denial):
            return respond(c)
        out = svc.tools.call(c.user_id, tool, args or {})
        if isinstance(out, dict) and out.get("error") == "permission_denied":
            return JSONResponse(status_code=403, content=out)
        return out

    return app
