from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .block3_storage import Block3RepositoryMixin
from .constants import MAX_BLOCK1_ROWS
from .maps import build_google_maps_url
from .models import (
    Block1Analysis,
    Block1Row,
    Block2Scenario,
    JurisdictionResearch,
    Project,
    input_fingerprint,
    jurisdiction_key,
    utc_now_iso,
)


def default_database_path() -> Path:
    configured = os.environ.get("AFRICAN_VILLAS_DATA_DIR")
    if configured:
        data_dir = Path(configured).expanduser().resolve()
    else:
        data_dir = Path(__file__).resolve().parents[2] / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "african_villas.db"


class Repository(Block3RepositoryMixin):
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path) if database_path else default_database_path()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    client_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS block1_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    country TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT '',
                    goal_code TEXT NOT NULL DEFAULT '',
                    map_url TEXT NOT NULL DEFAULT '',
                    user_note TEXT NOT NULL DEFAULT '',
                    input_hash TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    calculated_at TEXT,
                    UNIQUE(project_id, position)
                );

                CREATE TABLE IF NOT EXISTS analysis_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    row_id INTEGER NOT NULL REFERENCES block1_rows(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    input_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    is_current INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(row_id, version)
                );

                CREATE TABLE IF NOT EXISTS jurisdiction_cache (
                    jurisdiction_key TEXT PRIMARY KEY,
                    country TEXT NOT NULL,
                    region TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    model TEXT NOT NULL,
                    reasoning_effort TEXT NOT NULL,
                    source_policy TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS block2_scenarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    block1_row_id INTEGER NOT NULL REFERENCES block1_rows(id) ON DELETE CASCADE,
                    name TEXT NOT NULL DEFAULT 'Базовый сценарий',
                    initial_land_range TEXT NOT NULL DEFAULT '',
                    initial_land_m2 REAL NOT NULL DEFAULT 0,
                    object_land_range TEXT NOT NULL DEFAULT '',
                    object_land_m2 REAL NOT NULL DEFAULT 0,
                    footprint_m2 REAL NOT NULL DEFAULT 0,
                    floor_count INTEGER NOT NULL DEFAULT 1,
                    infrastructure_proximity_json TEXT NOT NULL DEFAULT '[]',
                    infrastructure_pct REAL NOT NULL DEFAULT 0,
                    other_losses_pct REAL NOT NULL DEFAULT 0,
                    average_unit_m2 REAL NOT NULL DEFAULT 0,
                    saleable_efficiency_pct REAL NOT NULL DEFAULT 80,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS block2_floors (
                    scenario_id INTEGER NOT NULL REFERENCES block2_scenarios(id) ON DELETE CASCADE,
                    floor_number INTEGER NOT NULL,
                    area_range TEXT NOT NULL DEFAULT '',
                    area_m2 REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(scenario_id, floor_number)
                );

                CREATE INDEX IF NOT EXISTS idx_block1_rows_project
                    ON block1_rows(project_id, position);
                CREATE INDEX IF NOT EXISTS idx_analysis_results_row
                    ON analysis_results(row_id, is_current);
                CREATE INDEX IF NOT EXISTS idx_cache_country_region
                    ON jurisdiction_cache(country, region);
                CREATE INDEX IF NOT EXISTS idx_block2_project
                    ON block2_scenarios(project_id, block1_row_id);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(block2_scenarios)")
            }
            if "infrastructure_proximity_json" not in columns:
                connection.execute(
                    """
                    ALTER TABLE block2_scenarios
                    ADD COLUMN infrastructure_proximity_json TEXT NOT NULL DEFAULT '[]'
                    """
                )
            self._initialize_block3(connection)

    def create_project(
        self, name: str, description: str = "", client_name: str = ""
    ) -> Project:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Название проекта обязательно")
        now = utc_now_iso()
        project_uid = str(uuid.uuid4())
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO projects(uid, name, description, client_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_uid, clean_name, description.strip(), client_name.strip(), now, now),
            )
            project_id = int(cursor.lastrowid)
        project = self.get_project(project_id)
        if project is None:
            raise RuntimeError("Не удалось создать проект")
        return project

    def get_project(self, project_id: int) -> Project | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return self._project_from_row(row) if row else None

    def list_projects(self) -> list[Project]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return [self._project_from_row(row) for row in rows]

    def project_progress(self, project_id: int) -> tuple[int, int]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'ready' THEN 1 ELSE 0 END) AS ready
                FROM block1_rows
                WHERE project_id = ?
                  AND (country <> '' OR region <> '' OR goal_code <> '')
                """,
                (project_id,),
            ).fetchone()
        return int(row["ready"] or 0), int(row["total"] or 0)

    def touch_project(self, project_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE projects SET updated_at = ? WHERE id = ?",
                (utc_now_iso(), project_id),
            )

    def create_block1_row(self, project_id: int) -> Block1Row:
        with self._connection() as connection:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM block1_rows WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0]
            )
            if count >= MAX_BLOCK1_ROWS:
                raise ValueError(f"В одном проекте допускается не более {MAX_BLOCK1_ROWS} строк")
            max_position = int(
                connection.execute(
                    "SELECT COALESCE(MAX(position), 0) FROM block1_rows WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0]
            )
            now = utc_now_iso()
            cursor = connection.execute(
                """
                INSERT INTO block1_rows(project_id, position, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (project_id, max_position + 1, now, now),
            )
            row_id = int(cursor.lastrowid)
        self.touch_project(project_id)
        row = self.get_block1_row(row_id)
        if row is None:
            raise RuntimeError("Не удалось создать строку")
        return row

    def get_block1_row(self, row_id: int) -> Block1Row | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM block1_rows WHERE id = ?", (row_id,)
            ).fetchone()
        return self._block1_row_from_row(row) if row else None

    def list_block1_rows(self, project_id: int) -> list[Block1Row]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM block1_rows WHERE project_id = ? ORDER BY position",
                (project_id,),
            ).fetchall()
        return [self._block1_row_from_row(row) for row in rows]

    def update_block1_input(
        self,
        row_id: int,
        *,
        country: str,
        region: str,
        goal_code: str,
        user_note: str = "",
    ) -> Block1Row:
        current = self.get_block1_row(row_id)
        if current is None:
            raise ValueError("Строка не найдена")

        country = country.strip()
        region = region.strip()
        goal_code = goal_code.strip()
        map_url = build_google_maps_url(country, region)
        new_hash = input_fingerprint(country, region, goal_code)
        changed = new_hash != current.input_hash
        is_empty = not any((country, region, goal_code))
        if is_empty:
            status = "draft"
        elif changed:
            status = "needs_calculation"
        else:
            status = current.status

        now = utc_now_iso()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE block1_rows
                SET country = ?, region = ?, goal_code = ?, map_url = ?, user_note = ?,
                    input_hash = ?, status = ?, error_message = '', updated_at = ?
                WHERE id = ?
                """,
                (
                    country,
                    region,
                    goal_code,
                    map_url,
                    user_note.strip(),
                    new_hash,
                    status,
                    now,
                    row_id,
                ),
            )
            if changed:
                connection.execute(
                    "UPDATE analysis_results SET is_current = 0 WHERE row_id = ?",
                    (row_id,),
                )
        self.touch_project(current.project_id)
        updated = self.get_block1_row(row_id)
        if updated is None:
            raise RuntimeError("Не удалось сохранить строку")
        return updated

    def set_row_status(self, row_id: int, status: str, error_message: str = "") -> None:
        row = self.get_block1_row(row_id)
        if row is None:
            return
        with self._connection() as connection:
            connection.execute(
                "UPDATE block1_rows SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                (status, error_message, utc_now_iso(), row_id),
            )
        self.touch_project(row.project_id)

    def save_analysis(self, row_id: int, analysis: Block1Analysis) -> None:
        row = self.get_block1_row(row_id)
        if row is None:
            raise ValueError("Строка не найдена")
        now = utc_now_iso()
        payload_json = analysis.model_dump_json()
        with self._connection() as connection:
            connection.execute(
                "UPDATE analysis_results SET is_current = 0 WHERE row_id = ?",
                (row_id,),
            )
            version = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM analysis_results WHERE row_id = ?",
                    (row_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO analysis_results(row_id, version, input_hash, payload_json, is_current, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (row_id, version, row.input_hash, payload_json, now),
            )
            connection.execute(
                """
                UPDATE block1_rows
                SET status = 'ready', error_message = '', calculated_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, row_id),
            )
        self.touch_project(row.project_id)

    def get_current_analysis(self, row_id: int) -> Block1Analysis | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM analysis_results
                WHERE row_id = ? AND is_current = 1
                ORDER BY version DESC LIMIT 1
                """,
                (row_id,),
            ).fetchone()
        if not row:
            return None
        return Block1Analysis.model_validate(json.loads(row["payload_json"]))

    def analysis_history_count(self, row_id: int) -> int:
        with self._connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM analysis_results WHERE row_id = ?", (row_id,)
                ).fetchone()[0]
            )

    @property
    def jurisdiction_cache_dir(self) -> Path:
        return self.database_path.parent / "jurisdiction_cache"

    def save_jurisdiction_research(self, research: JurisdictionResearch) -> None:
        key = jurisdiction_key(research.country, research.region)
        now = utc_now_iso()
        payload = research.model_dump_json(indent=2)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO jurisdiction_cache(
                    jurisdiction_key, country, region, checked_at, model,
                    reasoning_effort, source_policy, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jurisdiction_key) DO UPDATE SET
                    country = excluded.country,
                    region = excluded.region,
                    checked_at = excluded.checked_at,
                    model = excluded.model,
                    reasoning_effort = excluded.reasoning_effort,
                    source_policy = excluded.source_policy,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    research.country,
                    research.region,
                    research.checked_at,
                    research.model,
                    research.reasoning_effort,
                    research.source_policy,
                    payload,
                    now,
                    now,
                ),
            )

        # A readable copy makes the shared reference usable and recoverable outside SQLite.
        self.jurisdiction_cache_dir.mkdir(parents=True, exist_ok=True)
        (self.jurisdiction_cache_dir / f"{key}.json").write_text(
            payload, encoding="utf-8"
        )

    def get_jurisdiction_research(
        self, country: str, region: str
    ) -> JurisdictionResearch | None:
        key = jurisdiction_key(country, region)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM jurisdiction_cache WHERE jurisdiction_key = ?",
                (key,),
            ).fetchone()
        if not row:
            return None
        try:
            return JurisdictionResearch.model_validate_json(row["payload_json"])
        except (ValueError, TypeError):
            return None

    def cache_checked_at(self, country: str, region: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT checked_at FROM jurisdiction_cache WHERE jurisdiction_key = ?",
                (jurisdiction_key(country, region),),
            ).fetchone()
        return str(row["checked_at"]) if row else None

    def ensure_block2_scenarios(self, project_id: int) -> None:
        rows = [row for row in self.list_block1_rows(project_id) if not row.is_empty]
        with self._connection() as connection:
            now = utc_now_iso()
            for row in rows:
                existing_scenarios = connection.execute(
                    "SELECT id, floor_count FROM block2_scenarios WHERE block1_row_id = ?",
                    (row.id,),
                ).fetchall()
                if existing_scenarios:
                    target_count = 0 if row.goal_code == "LAND_INFRASTRUCTURE_RESALE" else 1
                    for existing in existing_scenarios:
                        current_count = int(existing["floor_count"])
                        if (target_count == 0 and current_count != 0) or (
                            target_count == 1 and current_count == 0
                        ):
                            connection.execute(
                                "UPDATE block2_scenarios SET floor_count = ?, updated_at = ? WHERE id = ?",
                                (target_count, now, int(existing["id"])),
                            )
                            if target_count == 0:
                                connection.execute(
                                    "DELETE FROM block2_floors WHERE scenario_id = ?",
                                    (int(existing["id"]),),
                                )
                            else:
                                connection.execute(
                                    """
                                    INSERT OR IGNORE INTO block2_floors(scenario_id, floor_number)
                                    VALUES (?, 1)
                                    """,
                                    (int(existing["id"]),),
                                )
                    continue
                floor_count = 0 if row.goal_code == "LAND_INFRASTRUCTURE_RESALE" else 1
                cursor = connection.execute(
                    """
                    INSERT INTO block2_scenarios(
                        project_id, block1_row_id, name, floor_count, created_at, updated_at
                    ) VALUES (?, ?, 'Базовый сценарий', ?, ?, ?)
                    """,
                    (project_id, row.id, floor_count, now, now),
                )
                scenario_id = int(cursor.lastrowid)
                for floor_number in range(1, floor_count + 1):
                    connection.execute(
                        """
                        INSERT INTO block2_floors(scenario_id, floor_number)
                        VALUES (?, ?)
                        """,
                        (scenario_id, floor_number),
                    )

    def list_block2_scenarios(self, project_id: int) -> list[Block2Scenario]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM block2_scenarios WHERE project_id = ? ORDER BY id",
                (project_id,),
            ).fetchall()
        return [Block2Scenario(**dict(row)) for row in rows]

    def create_block2_scenario(
        self, project_id: int, block1_row_id: int, name: str = "Новый сценарий"
    ) -> Block2Scenario:
        row = self.get_block1_row(block1_row_id)
        if row is None or row.project_id != project_id:
            raise ValueError("Строка блока №1 не найдена в этом проекте")
        floor_count = 0 if row.goal_code == "LAND_INFRASTRUCTURE_RESALE" else 1
        now = utc_now_iso()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO block2_scenarios(
                    project_id, block1_row_id, name, floor_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, block1_row_id, name.strip() or "Новый сценарий", floor_count, now, now),
            )
            scenario_id = int(cursor.lastrowid)
            for floor_number in range(1, floor_count + 1):
                connection.execute(
                    "INSERT INTO block2_floors(scenario_id, floor_number) VALUES (?, ?)",
                    (scenario_id, floor_number),
                )
        return self.get_block2_scenario(scenario_id)

    def get_block2_scenario(self, scenario_id: int) -> Block2Scenario:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM block2_scenarios WHERE id = ?", (scenario_id,)
            ).fetchone()
        if not row:
            raise ValueError("Сценарий не найден")
        return Block2Scenario(**dict(row))

    def update_block2_scenario(self, scenario_id: int, **values: object) -> None:
        allowed = {
            "name",
            "initial_land_range",
            "initial_land_m2",
            "object_land_range",
            "object_land_m2",
            "footprint_m2",
            "floor_count",
            "infrastructure_proximity_json",
            "infrastructure_pct",
            "other_losses_pct",
            "average_unit_m2",
            "saleable_efficiency_pct",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Неизвестные поля: {', '.join(sorted(unknown))}")
        if not values:
            return
        assignments = ", ".join(f"{name} = ?" for name in values)
        parameters = [*values.values(), utc_now_iso(), scenario_id]
        with self._connection() as connection:
            connection.execute(
                f"UPDATE block2_scenarios SET {assignments}, updated_at = ? WHERE id = ?",
                parameters,
            )

    def set_block2_floor_count(self, scenario_id: int, count: int) -> None:
        count = max(0, min(50, int(count)))
        with self._connection() as connection:
            connection.execute(
                "UPDATE block2_scenarios SET floor_count = ?, updated_at = ? WHERE id = ?",
                (count, utc_now_iso(), scenario_id),
            )
            connection.execute(
                "DELETE FROM block2_floors WHERE scenario_id = ? AND floor_number > ?",
                (scenario_id, count),
            )
            existing = {
                int(row[0])
                for row in connection.execute(
                    "SELECT floor_number FROM block2_floors WHERE scenario_id = ?",
                    (scenario_id,),
                ).fetchall()
            }
            for floor_number in range(1, count + 1):
                if floor_number not in existing:
                    connection.execute(
                        "INSERT INTO block2_floors(scenario_id, floor_number) VALUES (?, ?)",
                        (scenario_id, floor_number),
                    )

    def list_block2_floors(self, scenario_id: int) -> dict[int, tuple[str, float]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT floor_number, area_range, area_m2 FROM block2_floors
                WHERE scenario_id = ? ORDER BY floor_number
                """,
                (scenario_id,),
            ).fetchall()
        return {
            int(row["floor_number"]): (str(row["area_range"]), float(row["area_m2"]))
            for row in rows
        }

    def update_block2_floor(
        self, scenario_id: int, floor_number: int, area_range: str, area_m2: float
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO block2_floors(scenario_id, floor_number, area_range, area_m2)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scenario_id, floor_number) DO UPDATE SET
                    area_range = excluded.area_range,
                    area_m2 = excluded.area_m2
                """,
                (scenario_id, floor_number, area_range, float(area_m2)),
            )
            connection.execute(
                "UPDATE block2_scenarios SET updated_at = ? WHERE id = ?",
                (utc_now_iso(), scenario_id),
            )

    def delete_block2_scenario(self, scenario_id: int) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM block2_scenarios WHERE id = ?", (scenario_id,))

    def delete_block1_row(self, row_id: int) -> None:
        row = self.get_block1_row(row_id)
        if row is None:
            return
        with self._connection() as connection:
            connection.execute("DELETE FROM block1_rows WHERE id = ?", (row_id,))
        self.touch_project(row.project_id)

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> Project:
        return Project(**dict(row))

    @staticmethod
    def _block1_row_from_row(row: sqlite3.Row) -> Block1Row:
        return Block1Row(**dict(row))
