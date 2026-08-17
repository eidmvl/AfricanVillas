from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

from .block3 import DEVELOPMENT_CATEGORIES
from .models import (
    Block3Estimate,
    DevelopmentCost,
    EstimateRevision,
    LaborItem,
    MaterialItem,
    PriceQuote,
    ProjectDocument,
    ResourceItem,
    utc_now_iso,
)


class Block3RepositoryMixin:
    database_path: Path

    def _initialize_block3(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS block3_estimates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                scenario_id INTEGER NOT NULL UNIQUE REFERENCES block2_scenarios(id) ON DELETE CASCADE,
                currency TEXT NOT NULL DEFAULT 'USD',
                estimate_stage TEXT NOT NULL DEFAULT 'preliminary',
                parametric_rate_per_m2 REAL NOT NULL DEFAULT 0,
                schedule_days INTEGER NOT NULL DEFAULT 180,
                hours_per_day REAL NOT NULL DEFAULT 8,
                utilization_pct REAL NOT NULL DEFAULT 80,
                overhead_pct REAL NOT NULL DEFAULT 10,
                profit_pct REAL NOT NULL DEFAULT 10,
                contingency_pct REAL NOT NULL DEFAULT 12,
                tax_pct REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                accepted_revision_id INTEGER,
                block2_updated_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS block3_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estimate_id INTEGER NOT NULL REFERENCES block3_estimates(id) ON DELETE CASCADE,
                original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                page_count INTEGER NOT NULL DEFAULT 0,
                discipline TEXT NOT NULL DEFAULT 'Не определено',
                revision TEXT NOT NULL DEFAULT '',
                document_scope TEXT NOT NULL DEFAULT 'Требует подтверждения',
                units TEXT NOT NULL DEFAULT 'Требует подтверждения',
                scale_status TEXT NOT NULL DEFAULT 'Требует подтверждения',
                analysis_status TEXT NOT NULL DEFAULT 'not_analyzed',
                extracted_text TEXT NOT NULL DEFAULT '',
                analysis_json TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(estimate_id, sha256)
            );

            CREATE TABLE IF NOT EXISTS block3_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estimate_id INTEGER NOT NULL REFERENCES block3_estimates(id) ON DELETE CASCADE,
                work_package TEXT NOT NULL DEFAULT 'Прочее',
                description TEXT NOT NULL,
                specification TEXT NOT NULL DEFAULT '',
                quantity REAL NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT 'шт',
                waste_pct REAL NOT NULL DEFAULT 0,
                package_size REAL NOT NULL DEFAULT 1,
                multiplier REAL NOT NULL DEFAULT 1,
                scope TEXT NOT NULL DEFAULT 'Весь проект',
                source_document_id INTEGER REFERENCES block3_documents(id) ON DELETE SET NULL,
                source_page INTEGER,
                source_note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                confidence TEXT NOT NULL DEFAULT 'medium',
                is_manual INTEGER NOT NULL DEFAULT 1,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS block3_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estimate_id INTEGER NOT NULL REFERENCES block3_estimates(id) ON DELETE CASCADE,
                material_id INTEGER NOT NULL REFERENCES block3_materials(id) ON DELETE CASCADE,
                supplier TEXT NOT NULL DEFAULT '',
                product_name TEXT NOT NULL DEFAULT '',
                is_analog INTEGER NOT NULL DEFAULT 0,
                compatibility_status TEXT NOT NULL DEFAULT 'exact',
                currency TEXT NOT NULL DEFAULT 'USD',
                exchange_rate_to_estimate REAL NOT NULL DEFAULT 1,
                fx_observed_at TEXT NOT NULL DEFAULT '',
                fx_source_url TEXT NOT NULL DEFAULT '',
                unit_price REAL NOT NULL DEFAULT 0,
                price_quantity REAL NOT NULL DEFAULT 1,
                delivery_cost REAL NOT NULL DEFAULT 0,
                duty_cost REAL NOT NULL DEFAULT 0,
                tax_cost REAL NOT NULL DEFAULT 0,
                url TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL DEFAULT '',
                valid_until TEXT NOT NULL DEFAULT '',
                availability TEXT NOT NULL DEFAULT '',
                is_selected INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS block3_labor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estimate_id INTEGER NOT NULL REFERENCES block3_estimates(id) ON DELETE CASCADE,
                work_package TEXT NOT NULL DEFAULT 'Прочее',
                profession TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT 'шт',
                norm_hours REAL NOT NULL DEFAULT 0,
                productivity_factor REAL NOT NULL DEFAULT 1,
                planned_days INTEGER NOT NULL DEFAULT 0,
                hourly_rate REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS block3_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estimate_id INTEGER NOT NULL REFERENCES block3_estimates(id) ON DELETE CASCADE,
                category TEXT NOT NULL DEFAULT 'Прочее',
                description TEXT NOT NULL,
                calculation_method TEXT NOT NULL DEFAULT 'quantity',
                quantity REAL NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT 'шт',
                unit_rate REAL NOT NULL DEFAULT 0,
                duration REAL NOT NULL DEFAULT 0,
                includes_materials INTEGER NOT NULL DEFAULT 0,
                includes_labor INTEGER NOT NULL DEFAULT 0,
                includes_equipment INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS block3_development_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estimate_id INTEGER NOT NULL REFERENCES block3_estimates(id) ON DELETE CASCADE,
                category_code TEXT NOT NULL,
                label TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(estimate_id, category_code)
            );

            CREATE TABLE IF NOT EXISTS block3_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estimate_id INTEGER NOT NULL REFERENCES block3_estimates(id) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(estimate_id, version)
            );

            CREATE INDEX IF NOT EXISTS idx_block3_estimates_project ON block3_estimates(project_id);
            CREATE INDEX IF NOT EXISTS idx_block3_documents_estimate ON block3_documents(estimate_id);
            CREATE INDEX IF NOT EXISTS idx_block3_materials_estimate ON block3_materials(estimate_id);
            CREATE INDEX IF NOT EXISTS idx_block3_prices_material ON block3_prices(material_id, is_selected);
            CREATE INDEX IF NOT EXISTS idx_block3_labor_estimate ON block3_labor(estimate_id);
            CREATE INDEX IF NOT EXISTS idx_block3_resources_estimate ON block3_resources(estimate_id);
            """
        )
        price_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(block3_prices)").fetchall()
        }
        if "exchange_rate_to_estimate" not in price_columns:
            connection.execute(
                "ALTER TABLE block3_prices ADD COLUMN exchange_rate_to_estimate REAL NOT NULL DEFAULT 1"
            )
        if "fx_observed_at" not in price_columns:
            connection.execute(
                "ALTER TABLE block3_prices ADD COLUMN fx_observed_at TEXT NOT NULL DEFAULT ''"
            )
        if "fx_source_url" not in price_columns:
            connection.execute(
                "ALTER TABLE block3_prices ADD COLUMN fx_source_url TEXT NOT NULL DEFAULT ''"
            )

    def ensure_block3_estimates(self, project_id: int) -> None:
        now = utc_now_iso()
        with self._connection() as connection:  # type: ignore[attr-defined]
            scenarios = connection.execute(
                "SELECT id, updated_at FROM block2_scenarios WHERE project_id = ? ORDER BY id",
                (project_id,),
            ).fetchall()
            for scenario in scenarios:
                existing = connection.execute(
                    "SELECT id, block2_updated_at, status FROM block3_estimates WHERE scenario_id = ?",
                    (int(scenario["id"]),),
                ).fetchone()
                if existing is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO block3_estimates(
                            project_id, scenario_id, block2_updated_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (project_id, int(scenario["id"]), str(scenario["updated_at"]), now, now),
                    )
                    estimate_id = int(cursor.lastrowid)
                else:
                    estimate_id = int(existing["id"])
                    if str(existing["block2_updated_at"]) != str(scenario["updated_at"]):
                        connection.execute(
                            """
                            UPDATE block3_estimates
                            SET status = 'stale', block2_updated_at = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (str(scenario["updated_at"]), now, estimate_id),
                        )
                for code, label in DEVELOPMENT_CATEGORIES:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO block3_development_costs(
                            estimate_id, category_code, label, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (estimate_id, code, label, now, now),
                    )

    def list_block3_estimates(self, project_id: int) -> list[Block3Estimate]:
        self.ensure_block3_estimates(project_id)
        with self._connection() as connection:  # type: ignore[attr-defined]
            rows = connection.execute(
                "SELECT * FROM block3_estimates WHERE project_id = ? ORDER BY scenario_id",
                (project_id,),
            ).fetchall()
        return [Block3Estimate(**dict(row)) for row in rows]

    def get_block3_estimate(self, estimate_id: int) -> Block3Estimate:
        with self._connection() as connection:  # type: ignore[attr-defined]
            row = connection.execute(
                "SELECT * FROM block3_estimates WHERE id = ?", (estimate_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Смета блока №3 не найдена")
        return Block3Estimate(**dict(row))

    def get_block3_estimate_for_scenario(self, scenario_id: int) -> Block3Estimate:
        with self._connection() as connection:  # type: ignore[attr-defined]
            scenario = connection.execute(
                "SELECT project_id FROM block2_scenarios WHERE id = ?", (scenario_id,)
            ).fetchone()
        if scenario is None:
            raise ValueError("Сценарий блока №2 не найден")
        self.ensure_block3_estimates(int(scenario["project_id"]))
        with self._connection() as connection:  # type: ignore[attr-defined]
            row = connection.execute(
                "SELECT * FROM block3_estimates WHERE scenario_id = ?", (scenario_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("Не удалось создать смету блока №3")
        return Block3Estimate(**dict(row))

    def update_block3_estimate(self, estimate_id: int, **values: object) -> None:
        allowed = {
            "currency", "estimate_stage", "parametric_rate_per_m2", "schedule_days",
            "hours_per_day", "utilization_pct", "overhead_pct", "profit_pct",
            "contingency_pct", "tax_pct", "notes", "status", "accepted_revision_id",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Неизвестные поля блока №3: {', '.join(sorted(unknown))}")
        if not values:
            return
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self._connection() as connection:  # type: ignore[attr-defined]
            connection.execute(
                f"UPDATE block3_estimates SET {assignments}, updated_at = ? WHERE id = ?",
                [*values.values(), utc_now_iso(), estimate_id],
            )

    def project_documents_dir(self, estimate_id: int) -> Path:
        estimate = self.get_block3_estimate(estimate_id)
        project = self.get_project(estimate.project_id)  # type: ignore[attr-defined]
        if project is None:
            raise ValueError("Проект не найден")
        path = self.database_path.parent / "projects" / project.uid / "documents"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_block3_document(
        self,
        estimate_id: int,
        source_path: str | Path,
        *,
        sha256: str,
        size_bytes: int,
        page_count: int,
        extracted_text: str,
    ) -> ProjectDocument:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file() or source.suffix.casefold() != ".pdf":
            raise ValueError("Выберите существующий PDF-файл")
        with self._connection() as connection:  # type: ignore[attr-defined]
            existing = connection.execute(
                "SELECT * FROM block3_documents WHERE estimate_id = ? AND sha256 = ?",
                (estimate_id, sha256),
            ).fetchone()
        if existing is not None:
            return ProjectDocument(**dict(existing))

        safe_name = re.sub(r"[^\w.() -]+", "_", source.name, flags=re.UNICODE).strip(" .")
        target = self.project_documents_dir(estimate_id) / f"{sha256[:12]}__{safe_name or 'project.pdf'}"
        if not target.exists():
            shutil.copy2(source, target)
        now = utc_now_iso()
        with self._connection() as connection:  # type: ignore[attr-defined]
            cursor = connection.execute(
                """
                INSERT INTO block3_documents(
                    estimate_id, original_name, stored_path, sha256, size_bytes, page_count,
                    extracted_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (estimate_id, source.name, str(target), sha256, size_bytes, page_count,
                 extracted_text, now, now),
            )
            document_id = int(cursor.lastrowid)
        return self.get_block3_document(document_id)

    def get_block3_document(self, document_id: int) -> ProjectDocument:
        with self._connection() as connection:  # type: ignore[attr-defined]
            row = connection.execute(
                "SELECT * FROM block3_documents WHERE id = ?", (document_id,)
            ).fetchone()
        if row is None:
            raise ValueError("Документ не найден")
        return ProjectDocument(**dict(row))

    def list_block3_documents(self, estimate_id: int) -> list[ProjectDocument]:
        with self._connection() as connection:  # type: ignore[attr-defined]
            rows = connection.execute(
                "SELECT * FROM block3_documents WHERE estimate_id = ? ORDER BY id",
                (estimate_id,),
            ).fetchall()
        return [ProjectDocument(**dict(row)) for row in rows]

    def update_block3_document(self, document_id: int, **values: object) -> None:
        allowed = {"discipline", "revision", "document_scope", "units", "scale_status",
                   "analysis_status", "analysis_json", "error_message"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Неизвестные поля документа: {', '.join(sorted(unknown))}")
        if not values:
            return
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self._connection() as connection:  # type: ignore[attr-defined]
            connection.execute(
                f"UPDATE block3_documents SET {assignments}, updated_at = ? WHERE id = ?",
                [*values.values(), utc_now_iso(), document_id],
            )

    def delete_block3_document(self, document_id: int) -> None:
        with self._connection() as connection:  # type: ignore[attr-defined]
            connection.execute("DELETE FROM block3_documents WHERE id = ?", (document_id,))

    def clear_document_suggestions(self, estimate_id: int, document_id: int) -> None:
        """Remove only machine-generated rows before a forced document re-analysis."""
        marker = f"PDF:{document_id}:%"
        with self._connection() as connection:  # type: ignore[attr-defined]
            connection.execute(
                "DELETE FROM block3_materials WHERE estimate_id = ? AND source_document_id = ? AND is_manual = 0",
                (estimate_id, document_id),
            )
            connection.execute(
                "DELETE FROM block3_labor WHERE estimate_id = ? AND source LIKE ?",
                (estimate_id, marker),
            )

    def list_materials(self, estimate_id: int) -> list[MaterialItem]:
        return self._list_dataclasses("block3_materials", MaterialItem, estimate_id)

    def create_material(self, estimate_id: int, **values: object) -> MaterialItem:
        values = {"description": "Новый материал", **values}
        return self._create_item("block3_materials", MaterialItem, estimate_id, values)

    def update_material(self, item_id: int, **values: object) -> None:
        self._update_item("block3_materials", item_id, values)

    def delete_material(self, item_id: int) -> None:
        self._delete_item("block3_materials", item_id)

    def list_prices(self, estimate_id: int) -> list[PriceQuote]:
        return self._list_dataclasses("block3_prices", PriceQuote, estimate_id)

    def create_price(self, estimate_id: int, material_id: int, **values: object) -> PriceQuote:
        values = {"material_id": material_id, **values}
        quote = self._create_item("block3_prices", PriceQuote, estimate_id, values)
        if quote.is_selected:
            self.select_price(quote.id)
            quote = self.get_price(quote.id)
        return quote

    def get_price(self, quote_id: int) -> PriceQuote:
        with self._connection() as connection:  # type: ignore[attr-defined]
            row = connection.execute("SELECT * FROM block3_prices WHERE id = ?", (quote_id,)).fetchone()
        if row is None:
            raise ValueError("Цена не найдена")
        return PriceQuote(**dict(row))

    def update_price(self, quote_id: int, **values: object) -> None:
        selected = bool(values.pop("is_selected", False)) if "is_selected" in values else False
        self._update_item("block3_prices", quote_id, values)
        if selected:
            self.select_price(quote_id)

    def select_price(self, quote_id: int) -> None:
        quote = self.get_price(quote_id)
        with self._connection() as connection:  # type: ignore[attr-defined]
            connection.execute("UPDATE block3_prices SET is_selected = 0 WHERE material_id = ?", (quote.material_id,))
            connection.execute(
                "UPDATE block3_prices SET is_selected = 1, updated_at = ? WHERE id = ?",
                (utc_now_iso(), quote_id),
            )

    def delete_price(self, quote_id: int) -> None:
        self._delete_item("block3_prices", quote_id)

    def list_labor(self, estimate_id: int) -> list[LaborItem]:
        return self._list_dataclasses("block3_labor", LaborItem, estimate_id)

    def create_labor(self, estimate_id: int, **values: object) -> LaborItem:
        values = {"profession": "Новая профессия", **values}
        return self._create_item("block3_labor", LaborItem, estimate_id, values)

    def update_labor(self, item_id: int, **values: object) -> None:
        self._update_item("block3_labor", item_id, values)

    def delete_labor(self, item_id: int) -> None:
        self._delete_item("block3_labor", item_id)

    def list_resources(self, estimate_id: int) -> list[ResourceItem]:
        return self._list_dataclasses("block3_resources", ResourceItem, estimate_id)

    def create_resource(self, estimate_id: int, **values: object) -> ResourceItem:
        values = {"description": "Новый ресурс", **values}
        return self._create_item("block3_resources", ResourceItem, estimate_id, values)

    def update_resource(self, item_id: int, **values: object) -> None:
        self._update_item("block3_resources", item_id, values)

    def delete_resource(self, item_id: int) -> None:
        self._delete_item("block3_resources", item_id)

    def list_development_costs(self, estimate_id: int) -> list[DevelopmentCost]:
        return self._list_dataclasses("block3_development_costs", DevelopmentCost, estimate_id)

    def update_development_cost(self, item_id: int, **values: object) -> None:
        self._update_item("block3_development_costs", item_id, values)

    def save_estimate_revision(self, estimate_id: int, payload: dict[str, object]) -> EstimateRevision:
        now = utc_now_iso()
        with self._connection() as connection:  # type: ignore[attr-defined]
            version = int(connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM block3_revisions WHERE estimate_id = ?",
                (estimate_id,),
            ).fetchone()[0])
            cursor = connection.execute(
                "INSERT INTO block3_revisions(estimate_id, version, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (estimate_id, version, json.dumps(payload, ensure_ascii=False, sort_keys=True), now),
            )
            revision_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE block3_estimates SET accepted_revision_id = ?, status = 'accepted', updated_at = ? WHERE id = ?",
                (revision_id, now, estimate_id),
            )
        return EstimateRevision(revision_id, estimate_id, version, json.dumps(payload, ensure_ascii=False), now)

    def list_estimate_revisions(self, estimate_id: int) -> list[EstimateRevision]:
        with self._connection() as connection:  # type: ignore[attr-defined]
            rows = connection.execute(
                "SELECT * FROM block3_revisions WHERE estimate_id = ? ORDER BY version DESC",
                (estimate_id,),
            ).fetchall()
        return [EstimateRevision(**dict(row)) for row in rows]

    def _list_dataclasses(self, table: str, cls: type, estimate_id: int) -> list:
        with self._connection() as connection:  # type: ignore[attr-defined]
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE estimate_id = ? ORDER BY id", (estimate_id,)
            ).fetchall()
        return [cls(**dict(row)) for row in rows]

    def _create_item(self, table: str, cls: type, estimate_id: int, values: dict[str, object]):
        now = utc_now_iso()
        columns = ["estimate_id", *values, "created_at", "updated_at"]
        params = [estimate_id, *values.values(), now, now]
        placeholders = ", ".join("?" for _ in columns)
        with self._connection() as connection:  # type: ignore[attr-defined]
            cursor = connection.execute(
                f"INSERT INTO {table}({', '.join(columns)}) VALUES ({placeholders})", params
            )
            item_id = int(cursor.lastrowid)
            row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
        return cls(**dict(row))

    def _update_item(self, table: str, item_id: int, values: dict[str, object]) -> None:
        if not values:
            return
        columns = {
            str(row["name"])
            for row in self._connection_table_info(table)
        }
        allowed = columns - {"id", "estimate_id", "created_at", "updated_at"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Неизвестные поля: {', '.join(sorted(unknown))}")
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self._connection() as connection:  # type: ignore[attr-defined]
            connection.execute(
                f"UPDATE {table} SET {assignments}, updated_at = ? WHERE id = ?",
                [*values.values(), utc_now_iso(), item_id],
            )

    def _connection_table_info(self, table: str) -> list[sqlite3.Row]:
        with self._connection() as connection:  # type: ignore[attr-defined]
            return connection.execute(f"PRAGMA table_info({table})").fetchall()

    def _delete_item(self, table: str, item_id: int) -> None:
        with self._connection() as connection:  # type: ignore[attr-defined]
            connection.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
