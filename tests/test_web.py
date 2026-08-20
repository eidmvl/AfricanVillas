from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from african_villas.database import Repository
from african_villas.web import WebSettings, create_app


AUTH = ("tester", "secret-password")


def web_client(tmp_path: Path) -> TestClient:
    settings = WebSettings(
        username=AUTH[0],
        password=AUTH[1],
        session_secret="test-session-secret-at-least-32-bytes",
        allowed_hosts=("testserver", "localhost", "127.0.0.1"),
        public_origin="http://testserver",
        max_upload_bytes=5 * 1024 * 1024,
    )
    return TestClient(
        create_app(repository=Repository(tmp_path / "web.db"), settings=settings)
    )


def blank_pdf() -> bytes:
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(stream)
    return stream.getvalue()


def test_health_is_public_but_application_requires_auth(tmp_path: Path) -> None:
    with web_client(tmp_path) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["service"] == "african-villas-web"
        assert health.json()["release"] == "development"
        assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"

        unauthorized = client.get("/api/config")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == 'Basic realm="African Villas"'

        authorized = client.get("/api/config", auth=AUTH)
        assert authorized.status_code == 200
        assert authorized.json()["countries"]
        assert authorized.headers["x-frame-options"] == "DENY"


def test_login_creates_http_only_session(tmp_path: Path) -> None:
    with web_client(tmp_path) as client:
        response = client.post(
            "/api/login", json={"username": AUTH[0], "password": AUTH[1]}
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "av_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie

        projects = client.get("/api/projects")
        assert projects.status_code == 200


def test_project_block1_block2_and_pdf_flow(tmp_path: Path) -> None:
    with web_client(tmp_path) as client:
        created = client.post(
            "/api/projects",
            auth=AUTH,
            json={"name": "Zanzibar", "client_name": "Investor", "description": "Villas"},
        )
        assert created.status_code == 201
        project = created.json()
        project_id = project["project"]["id"]
        row_id = project["rows"][0]["id"]

        updated = client.put(
            f"/api/rows/{row_id}",
            auth=AUTH,
            json={
                "country": "Танзания",
                "region": "Занзибар",
                "goal_code": "VILLAS_FOR_SALE",
                "user_note": "Ocean side",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "needs_calculation"

        detail = client.get(f"/api/projects/{project_id}", auth=AUTH).json()
        assert len(detail["scenarios"]) == 1
        scenario = detail["scenarios"][0]
        scenario_id = scenario["id"]

        scenario_update = client.put(
            f"/api/scenarios/{scenario_id}",
            auth=AUTH,
            json={
                "initial_land_m2": 10_000,
                "object_land_m2": 500,
                "footprint_m2": 150,
                "infrastructure_pct": 20,
                "other_losses_pct": 5,
                "floor_count": 2,
                "floors": [
                    {"floor_number": 1, "area_range": "", "area_m2": 150},
                    {"floor_number": 2, "area_range": "", "area_m2": 140},
                ],
            },
        )
        assert scenario_update.status_code == 200

        detail = client.get(f"/api/projects/{project_id}", auth=AUTH).json()
        calculation = detail["scenarios"][0]["calculation"]
        assert calculation["usable_land_m2"] == 7_500
        assert calculation["building_count"] == 15
        estimate_id = detail["estimates"][0]["id"]

        uploaded = client.post(
            f"/api/estimates/{estimate_id}/documents",
            auth=AUTH,
            files={"upload": ("concept.pdf", blank_pdf(), "application/pdf")},
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["original_name"] == "concept.pdf"
        assert uploaded.json()["page_count"] == 1
        assert "stored_path" not in uploaded.json()

        index = client.get("/", auth=AUTH)
        assert index.status_code == 200
        assert "African Villas" in index.text


def test_mutating_request_rejects_foreign_origin(tmp_path: Path) -> None:
    with web_client(tmp_path) as client:
        response = client.post(
            "/api/projects",
            auth=AUTH,
            headers={"Origin": "https://evil.example"},
            json={"name": "Blocked"},
        )
        assert response.status_code == 403
