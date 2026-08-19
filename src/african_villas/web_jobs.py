from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from .analysis import AsyncCodexAnalyzer, assemble_block1_analysis
from .database import Repository
from .models import Block1Row, jurisdiction_key, utc_now_iso


@dataclass(slots=True)
class JobState:
    id: str
    kind: str
    project_id: int
    status: str
    message: str
    created_at: str
    started_at: str = ""
    finished_at: str = ""
    completed: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WebJobManager:
    """Own in-process jobs for the single-worker web deployment."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self._jobs: dict[str, JobState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._active_projects: dict[int, str] = {}

    def get(self, job_id: str) -> JobState | None:
        return self._jobs.get(job_id)

    def active_for_project(self, project_id: int) -> JobState | None:
        job_id = self._active_projects.get(project_id)
        return self._jobs.get(job_id) if job_id else None

    def start_block1(self, project_id: int, *, mode: str, force: bool) -> JobState:
        active = self.active_for_project(project_id)
        if active and active.status in {"queued", "running"}:
            return active
        rows = [row for row in self.repository.list_block1_rows(project_id) if not row.is_empty]
        if not rows:
            raise ValueError("Добавьте хотя бы одну заполненную юрисдикцию")
        invalid = [row for row in rows if row.missing_fields()]
        if invalid:
            raise ValueError("Заполните страну, регион и цель во всех строках")
        if mode not in {"standard", "deep"}:
            raise ValueError("Неизвестный режим анализа")

        job = JobState(
            id=uuid.uuid4().hex,
            kind="block1",
            project_id=project_id,
            status="queued",
            message="Анализ поставлен в очередь",
            created_at=utc_now_iso(),
        )
        self._jobs[job.id] = job
        self._active_projects[project_id] = job.id
        task = asyncio.create_task(self._run_block1(job, rows, mode=mode, force=force))
        self._tasks[job.id] = task
        task.add_done_callback(lambda _task, job_id=job.id: self._tasks.pop(job_id, None))
        self._trim_history()
        return job

    async def _run_block1(
        self,
        job: JobState,
        rows: list[Block1Row],
        *,
        mode: str,
        force: bool,
    ) -> None:
        job.status = "running"
        job.started_at = utc_now_iso()
        job.message = "Подготовка юрисдикций"
        try:
            groups: dict[str, list[Block1Row]] = {}
            for row in rows:
                groups.setdefault(jurisdiction_key(row.country, row.region), []).append(row)

            pending: list[list[Block1Row]] = []
            for group_rows in groups.values():
                representative = group_rows[0]
                cached = None if force else self.repository.get_jurisdiction_research(
                    representative.country, representative.region
                )
                if cached is None:
                    pending.append(group_rows)
                    continue
                for row in group_rows:
                    self.repository.save_analysis(
                        row.id, assemble_block1_analysis(cached, row.goal_label)
                    )
                    job.completed += 1

            semaphore = asyncio.Semaphore(2)
            if pending:
                async with AsyncCodexAnalyzer() as analyzer:

                    async def research(group_rows: list[Block1Row]) -> None:
                        representative = group_rows[0]
                        async with semaphore:
                            for row in group_rows:
                                self.repository.set_row_status(row.id, "queued")

                            def notify(status: str, message: str) -> None:
                                job.message = message
                                for member in group_rows:
                                    self.repository.set_row_status(member.id, status)

                            try:
                                result = await analyzer.analyze_jurisdiction(
                                    representative.country,
                                    representative.region,
                                    [row.goal_label for row in group_rows],
                                    mode,
                                    notify,
                                )
                                self.repository.save_jurisdiction_research(result)
                                for row in group_rows:
                                    self.repository.save_analysis(
                                        row.id,
                                        assemble_block1_analysis(result, row.goal_label),
                                    )
                                    job.completed += 1
                            except Exception as exc:  # noqa: BLE001 - job isolation boundary
                                message = str(exc).strip() or exc.__class__.__name__
                                for row in group_rows:
                                    self.repository.set_row_status(row.id, "error", message)
                                    job.failed += 1

                    await asyncio.gather(*(research(group) for group in pending))

            if job.failed and job.completed:
                job.status = "partial"
                job.message = "Анализ завершён частично"
            elif job.failed:
                job.status = "error"
                job.message = "Анализ завершился с ошибками"
            else:
                job.status = "ready"
                job.message = "Анализ завершён"
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.message = "Задача остановлена при завершении сервера"
            raise
        except Exception as exc:  # noqa: BLE001 - SDK startup and job boundary
            job.status = "error"
            job.message = str(exc).strip() or exc.__class__.__name__
            for row in rows:
                if self.repository.get_block1_row(row.id) is not None:
                    self.repository.set_row_status(row.id, "error", job.message)
                    job.failed += 1
        finally:
            job.finished_at = utc_now_iso()
            if self._active_projects.get(job.project_id) == job.id:
                self._active_projects.pop(job.project_id, None)

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _trim_history(self) -> None:
        if len(self._jobs) <= 100:
            return
        inactive = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status not in {"queued", "running"}
        ]
        for job_id in inactive[: len(self._jobs) - 100]:
            self._jobs.pop(job_id, None)
