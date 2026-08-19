from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .block2 import calculate_block2
from .constants import AFRICAN_COUNTRIES_RU, PROJECT_GOALS, STATUS_LABELS
from .database import Repository
from .pdf_pipeline import inspect_pdf
from .web_jobs import WebJobManager


@dataclass(frozen=True, slots=True)
class WebSettings:
    username: str
    password: str
    session_secret: str
    allowed_hosts: tuple[str, ...]
    public_origin: str
    max_upload_bytes: int = 50 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "WebSettings":
        hosts = tuple(
            item.strip()
            for item in os.environ.get(
                "AFRICAN_VILLAS_ALLOWED_HOSTS",
                "villas.bimplatforma.ru,localhost,127.0.0.1",
            ).split(",")
            if item.strip()
        )
        return cls(
            username=os.environ.get("AFRICAN_VILLAS_WEB_USERNAME", "admin").strip(),
            password=os.environ.get("AFRICAN_VILLAS_WEB_PASSWORD", ""),
            session_secret=os.environ.get("AFRICAN_VILLAS_SESSION_SECRET", ""),
            allowed_hosts=hosts,
            public_origin=os.environ.get(
                "AFRICAN_VILLAS_PUBLIC_ORIGIN", "https://villas.bimplatforma.ru"
            ).rstrip("/"),
            max_upload_bytes=int(
                os.environ.get("AFRICAN_VILLAS_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
            ),
        )


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    client_name: str = Field(default="", max_length=300)


class RowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = Field(default="", max_length=200)
    region: str = Field(default="", max_length=300)
    goal_code: str = Field(default="", max_length=100)
    user_note: str = Field(default="", max_length=4000)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "standard"
    force: bool = False


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000)


class ScenarioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block1_row_id: int
    name: str = Field(default="Новый сценарий", max_length=200)


class FloorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    floor_number: int = Field(ge=1, le=50)
    area_range: str = Field(default="", max_length=100)
    area_m2: float = Field(ge=0, le=1_000_000)


class ScenarioUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=200)
    initial_land_range: str | None = Field(default=None, max_length=100)
    initial_land_m2: float | None = Field(default=None, ge=0, le=100_000_000)
    object_land_range: str | None = Field(default=None, max_length=100)
    object_land_m2: float | None = Field(default=None, ge=0, le=100_000_000)
    footprint_m2: float | None = Field(default=None, ge=0, le=100_000_000)
    floor_count: int | None = Field(default=None, ge=0, le=50)
    infrastructure_pct: float | None = Field(default=None, ge=0, le=100)
    other_losses_pct: float | None = Field(default=None, ge=0, le=100)
    average_unit_m2: float | None = Field(default=None, ge=0, le=1_000_000)
    saleable_efficiency_pct: float | None = Field(default=None, ge=0, le=100)
    floors: list[FloorInput] | None = None


class EstimateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str | None = Field(default=None, max_length=12)
    estimate_stage: str | None = Field(default=None, max_length=50)
    parametric_rate_per_m2: float | None = Field(default=None, ge=0)
    schedule_days: int | None = Field(default=None, ge=0, le=100_000)
    hours_per_day: float | None = Field(default=None, ge=0, le=24)
    utilization_pct: float | None = Field(default=None, ge=0, le=100)
    overhead_pct: float | None = Field(default=None, ge=0, le=1000)
    profit_pct: float | None = Field(default=None, ge=0, le=1000)
    contingency_pct: float | None = Field(default=None, ge=0, le=1000)
    tax_pct: float | None = Field(default=None, ge=0, le=1000)
    notes: str | None = Field(default=None, max_length=10_000)


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    return tuple(decoded.split(":", 1))  # type: ignore[return-value]


def _new_session(config: WebSettings, lifetime_seconds: int = 12 * 60 * 60) -> str:
    expires = int(time.time()) + lifetime_seconds
    payload = f"{config.username}|{expires}"
    signature = hmac.new(
        config.session_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8")).decode("ascii")


def _valid_session(request: Request, config: WebSettings) -> bool:
    raw = request.cookies.get("av_session", "")
    if not raw or not config.session_secret:
        return False
    try:
        username, expires_text, signature = base64.urlsafe_b64decode(
            raw.encode("ascii")
        ).decode("utf-8").split("|", 2)
        expires = int(expires_text)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return False
    payload = f"{username}|{expires}"
    expected = hmac.new(
        config.session_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return bool(
        expires >= int(time.time())
        and hmac.compare_digest(username, config.username)
        and hmac.compare_digest(signature, expected)
    )


def _safe_document(document: object) -> dict[str, Any]:
    payload = asdict(document)  # type: ignore[arg-type]
    payload.pop("stored_path", None)
    payload.pop("extracted_text", None)
    payload.pop("analysis_json", None)
    return payload


def _project_payload(repository: Repository, project_id: int) -> dict[str, Any]:
    project = repository.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден")

    rows = repository.list_block1_rows(project_id)
    row_by_id = {row.id: row for row in rows}
    row_payloads: list[dict[str, Any]] = []
    for row in rows:
        item = asdict(row)
        item["goal_label"] = row.goal_label
        item["status_label"] = STATUS_LABELS.get(row.status, row.status)
        analysis = repository.get_current_analysis(row.id)
        item["analysis"] = analysis.model_dump(mode="json") if analysis else None
        row_payloads.append(item)

    repository.ensure_block2_scenarios(project_id)
    scenarios: list[dict[str, Any]] = []
    for scenario in repository.list_block2_scenarios(project_id):
        floors = repository.list_block2_floors(scenario.id)
        row = row_by_id.get(scenario.block1_row_id)
        research = (
            repository.get_jurisdiction_research(row.country, row.region) if row else None
        )
        calculation = calculate_block2(
            scenario,
            row.goal_code if row else "",
            [floors[index][1] for index in sorted(floors)],
            research.local_rules if research else None,
        )
        item = asdict(scenario)
        item["floors"] = [
            {"floor_number": number, "area_range": values[0], "area_m2": values[1]}
            for number, values in floors.items()
        ]
        item["calculation"] = asdict(calculation)
        item["jurisdiction"] = (
            f"{row.country}, {row.region}" if row else "Удалённая юрисдикция"
        )
        scenarios.append(item)

    estimates: list[dict[str, Any]] = []
    for estimate in repository.list_block3_estimates(project_id):
        item = asdict(estimate)
        item["documents"] = [
            _safe_document(document)
            for document in repository.list_block3_documents(estimate.id)
        ]
        item["material_count"] = len(repository.list_materials(estimate.id))
        item["price_count"] = len(repository.list_prices(estimate.id))
        item["labor_count"] = len(repository.list_labor(estimate.id))
        item["resource_count"] = len(repository.list_resources(estimate.id))
        estimates.append(item)

    ready, total = repository.project_progress(project_id)
    return {
        "project": asdict(project),
        "progress": {"ready": ready, "total": total},
        "rows": row_payloads,
        "scenarios": scenarios,
        "estimates": estimates,
    }


def create_app(
    *,
    repository: Repository | None = None,
    settings: WebSettings | None = None,
) -> FastAPI:
    repo = repository or Repository()
    config = settings or WebSettings.from_env()
    jobs = WebJobManager(repo)
    static_dir = Path(__file__).with_name("web_static")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await jobs.shutdown()

    web_app = FastAPI(
        title="African Villas Web",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    web_app.state.repository = repo
    web_app.state.settings = config
    web_app.state.jobs = jobs
    web_app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.allowed_hosts))

    @web_app.middleware("http")
    async def security(request: Request, call_next):  # type: ignore[no-untyped-def]
        public_paths = {
            "/health",
            "/robots.txt",
            "/login",
            "/login.js",
            "/styles.css",
            "/api/login",
        }
        if request.url.path not in public_paths:
            if not config.password or not config.session_secret:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Web authentication is not configured"},
                )
            credentials = _basic_credentials(request)
            basic_valid = bool(
                credentials
                and hmac.compare_digest(credentials[0], config.username)
                and hmac.compare_digest(credentials[1], config.password)
            )
            if not basic_valid and not _valid_session(request, config):
                if not request.url.path.startswith("/api/"):
                    return RedirectResponse("/login", status_code=303)
                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="African Villas"'},
                )
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                origin = request.headers.get("origin")
                if origin and origin.rstrip("/") != config.public_origin:
                    host = urlparse(origin).hostname or ""
                    if host not in config.allowed_hosts:
                        return JSONResponse(status_code=403, content={"detail": "Bad origin"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @web_app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @web_app.get("/health")
    async def health() -> dict[str, Any]:
        try:
            repo.list_projects()
            database = "ok"
        except Exception:  # noqa: BLE001 - health boundary must not expose details
            database = "error"
        return {
            "ok": database == "ok",
            "service": "african-villas-web",
            "version": __version__,
            "database": database,
        }

    @web_app.get("/robots.txt", include_in_schema=False)
    async def robots() -> Response:
        return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")

    @web_app.get("/api/config")
    async def api_config() -> dict[str, Any]:
        return {
            "countries": AFRICAN_COUNTRIES_RU,
            "goals": [{"code": code, "label": label} for code, label in PROJECT_GOALS],
            "version": __version__,
        }

    @web_app.post("/api/login")
    async def login(payload: LoginRequest) -> Response:
        if not config.password or not config.session_secret:
            raise HTTPException(status_code=503, detail="Авторизация не настроена")
        valid = hmac.compare_digest(payload.username, config.username) and hmac.compare_digest(
            payload.password, config.password
        )
        if not valid:
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "av_session",
            _new_session(config),
            max_age=12 * 60 * 60,
            httponly=True,
            secure=config.public_origin.startswith("https://"),
            samesite="strict",
            path="/",
        )
        return response

    @web_app.post("/api/logout", status_code=204)
    async def logout() -> Response:
        response = Response(status_code=204)
        response.delete_cookie("av_session", path="/")
        return response

    @web_app.get("/api/projects")
    async def list_projects() -> list[dict[str, Any]]:
        result = []
        for project in repo.list_projects():
            ready, total = repo.project_progress(project.id)
            result.append({**asdict(project), "progress": {"ready": ready, "total": total}})
        return result

    @web_app.post("/api/projects", status_code=201)
    async def create_project(payload: ProjectCreate) -> dict[str, Any]:
        project = repo.create_project(payload.name, payload.description, payload.client_name)
        repo.create_block1_row(project.id)
        return _project_payload(repo, project.id)

    @web_app.get("/api/projects/{project_id}")
    async def get_project(project_id: int) -> dict[str, Any]:
        return _project_payload(repo, project_id)

    @web_app.post("/api/projects/{project_id}/rows", status_code=201)
    async def create_row(project_id: int) -> dict[str, Any]:
        if repo.get_project(project_id) is None:
            raise HTTPException(status_code=404, detail="Проект не найден")
        return asdict(repo.create_block1_row(project_id))

    @web_app.put("/api/rows/{row_id}")
    async def update_row(row_id: int, payload: RowUpdate) -> dict[str, Any]:
        goal_codes = {code for code, _label in PROJECT_GOALS}
        if payload.goal_code and payload.goal_code not in goal_codes:
            raise ValueError("Неизвестная цель проекта")
        return asdict(
            repo.update_block1_input(
                row_id,
                country=payload.country,
                region=payload.region,
                goal_code=payload.goal_code,
                user_note=payload.user_note,
            )
        )

    @web_app.delete("/api/rows/{row_id}", status_code=204)
    async def delete_row(row_id: int) -> Response:
        if repo.get_block1_row(row_id) is None:
            raise HTTPException(status_code=404, detail="Строка не найдена")
        repo.delete_block1_row(row_id)
        return Response(status_code=204)

    @web_app.post("/api/projects/{project_id}/analyze", status_code=202)
    async def analyze_project(project_id: int, payload: AnalyzeRequest) -> dict[str, Any]:
        if repo.get_project(project_id) is None:
            raise HTTPException(status_code=404, detail="Проект не найден")
        return jobs.start_block1(
            project_id, mode=payload.mode, force=payload.force
        ).to_dict()

    @web_app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return job.to_dict()

    @web_app.post("/api/projects/{project_id}/scenarios", status_code=201)
    async def create_scenario(project_id: int, payload: ScenarioCreate) -> dict[str, Any]:
        return asdict(
            repo.create_block2_scenario(project_id, payload.block1_row_id, payload.name)
        )

    @web_app.put("/api/scenarios/{scenario_id}")
    async def update_scenario(scenario_id: int, payload: ScenarioUpdate) -> dict[str, Any]:
        repo.get_block2_scenario(scenario_id)
        values = payload.model_dump(exclude_unset=True)
        floors = values.pop("floors", None)
        floor_count = values.pop("floor_count", None)
        values = {key: value for key, value in values.items() if value is not None}
        if values:
            repo.update_block2_scenario(scenario_id, **values)
        if floor_count is not None:
            repo.set_block2_floor_count(scenario_id, floor_count)
        if floors is not None:
            for floor in floors:
                repo.update_block2_floor(
                    scenario_id,
                    floor["floor_number"],
                    floor["area_range"],
                    floor["area_m2"],
                )
        return asdict(repo.get_block2_scenario(scenario_id))

    @web_app.delete("/api/scenarios/{scenario_id}", status_code=204)
    async def delete_scenario(scenario_id: int) -> Response:
        repo.get_block2_scenario(scenario_id)
        repo.delete_block2_scenario(scenario_id)
        return Response(status_code=204)

    @web_app.put("/api/estimates/{estimate_id}")
    async def update_estimate(estimate_id: int, payload: EstimateUpdate) -> dict[str, Any]:
        repo.get_block3_estimate(estimate_id)
        values = {
            key: value
            for key, value in payload.model_dump(exclude_unset=True).items()
            if value is not None
        }
        repo.update_block3_estimate(estimate_id, **values)
        return asdict(repo.get_block3_estimate(estimate_id))

    @web_app.post("/api/estimates/{estimate_id}/documents", status_code=201)
    async def upload_document(
        estimate_id: int,
        upload: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        repo.get_block3_estimate(estimate_id)
        filename = Path(upload.filename or "project.pdf").name
        safe_name = re.sub(r"[^\w.() -]+", "_", filename, flags=re.UNICODE).strip(" .")
        if not safe_name.lower().endswith(".pdf"):
            raise ValueError("Разрешены только PDF-файлы")
        temp_root = repo.database_path.parent / "tmp" / "uploads"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as temp_dir:
            source = Path(temp_dir) / (safe_name or "project.pdf")
            total = 0
            with source.open("wb") as stream:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > config.max_upload_bytes:
                        raise ValueError("PDF превышает допустимый размер")
                    stream.write(chunk)
            inspection = inspect_pdf(source)
            document = repo.add_block3_document(
                estimate_id,
                source,
                sha256=inspection.sha256,
                size_bytes=inspection.size_bytes,
                page_count=inspection.page_count,
                extracted_text=inspection.extracted_text,
            )
        await upload.close()
        return _safe_document(document)

    @web_app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @web_app.get("/login")
    async def login_page() -> FileResponse:
        return FileResponse(static_dir / "login.html")

    @web_app.get("/app.js")
    async def javascript() -> FileResponse:
        return FileResponse(static_dir / "app.js", media_type="application/javascript")

    @web_app.get("/login.js")
    async def login_javascript() -> FileResponse:
        return FileResponse(static_dir / "login.js", media_type="application/javascript")

    @web_app.get("/styles.css")
    async def stylesheet() -> FileResponse:
        return FileResponse(static_dir / "styles.css", media_type="text/css")

    return web_app


app = create_app()
