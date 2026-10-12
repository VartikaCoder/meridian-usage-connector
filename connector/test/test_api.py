"""HTTP layer: status codes, headers and bodies. Thin by design; logic is tested elsewhere."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from meridian_usage.app import create_app
from meridian_usage.settings import Settings
from tests.test_permissions import CHLOE, DANIEL, MEI, PRIYA


@pytest.fixture
def client(settings: Settings, tmp_path: Path) -> TestClient:
    app = create_app(replace(settings, environment="prod", db_path=tmp_path / "prod.sqlite3"))
    return TestClient(app)


def as_(user: str) -> dict[str, str]:
    return {"X-Meridian-User": user}


def test_health_and_page(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok", "environment": "prod"}
    page = client.get("/")
    assert page.status_code == 200 and "Meridian" in page.text


def test_no_identity_is_403_denial(client: TestClient) -> None:
    r = client.get("/api/me")
    assert r.status_code == 403 and r.json()["error"] == "permission_denied"


def test_identity_by_email(client: TestClient) -> None:
    r = client.get("/api/me", headers=as_("priya.nair@meridianls.example"))
    assert r.status_code == 200 and r.json()["user_id"] == PRIYA


def test_usage_by_group(client: TestClient) -> None:
    r = client.get("/api/usage/by-group", params={"period": "2026-09"}, headers=as_(CHLOE))
    assert r.status_code == 200 and len(r.json()["groups"]) == 6


def test_cross_group_weekly_is_403(client: TestClient) -> None:
    r = client.get("/api/usage/weekly", params={"group": "finance"}, headers=as_(PRIYA))
    assert r.status_code == 403
    assert set(r.json()) <= {"error", "reason", "required_scope"}


def test_member_is_403_with_scope(client: TestClient) -> None:
    r = client.get("/api/usage/by-group", params={"period": "2026-09"}, headers=as_(DANIEL))
    assert r.status_code == 403 and r.json()["required_scope"] == "usage:read"


def test_bad_period_is_400(client: TestClient) -> None:
    r = client.get("/api/usage/by-group", params={"period": "2026-W99"}, headers=as_(CHLOE))
    assert r.status_code == 400 and r.json()["error"] == "invalid_argument"


class TestCapabilityWrites:
    url = "/api/capabilities/clinical-operations/web-search"

    def test_lead_denied_in_prod(self, client: TestClient) -> None:
        r = client.put(self.url, json={"enabled": False}, headers=as_(PRIYA))
        assert r.status_code == 403 and r.json()["required_scope"] == "capabilities:write"

    def test_admin_allowed_and_persisted(self, client: TestClient) -> None:
        r = client.put(self.url, json={"enabled": False}, headers=as_(CHLOE))
        assert r.status_code == 200 and r.json()["enabled"] is False
        states = client.get(
            "/api/capabilities", params={"group": "clinical-operations"}, headers=as_(CHLOE)
        ).json()
        assert {s["capability"]: s["enabled"] for s in states}["web-search"] is False

    @pytest.mark.parametrize(
        "body", [{"enabled": "yes"}, {"enabled": 1}, {}, {"enabled": True, "x": 1}]
    )
    def test_body_is_strict(self, client: TestClient, body: dict[str, object]) -> None:
        assert client.put(self.url, json=body, headers=as_(CHLOE)).status_code == 422

    def test_unknown_capability_is_400(self, client: TestClient) -> None:
        r = client.put("/api/capabilities/hr/teleport", json={"enabled": True}, headers=as_(CHLOE))
        assert r.status_code == 400


class TestConnectorEndpoint:
    def test_allowed(self, client: TestClient) -> None:
        r = client.post(
            "/api/connector/tools/usage_summary", json={"period": "2026-09"}, headers=as_(PRIYA)
        )
        assert r.status_code == 200 and r.json()["group"] == "clinical-operations"

    def test_cross_group_denied(self, client: TestClient) -> None:
        r = client.post(
            "/api/connector/tools/top_users",
            json={"group": "finance", "period": "2026-09"},
            headers=as_(PRIYA),
        )
        assert r.status_code == 403 and r.json()["error"] == "permission_denied"

    def test_connector_disabled_for_group(self, client: TestClient) -> None:
        r = client.post("/api/connector/tools/capability_status", json={}, headers=as_(MEI))
        assert r.status_code == 403 and "not enabled" in r.json()["reason"]

    def test_no_body_is_fine_for_tools_without_args(self, client: TestClient) -> None:
        r = client.post("/api/connector/tools/capability_status", headers=as_(PRIYA))
        assert r.status_code == 200 and len(r.json()) == 3

    def test_unknown_tool_is_400(self, client: TestClient) -> None:
        r = client.post("/api/connector/tools/delete_everything", json={}, headers=as_(CHLOE))
        assert r.status_code == 400
