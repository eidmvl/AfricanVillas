from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, QThread, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .analysis import codex_sdk_available
from .block2 import calculate_block2
from .block3 import (
    DEVELOPMENT_CATEGORIES,
    UNITS,
    WORK_PACKAGES,
    EstimateSummary,
    calculate_estimate,
    labor_hours,
    material_cost,
    required_workers,
    resource_cost,
)
from .block3_analysis import Block3CodexAnalyzer, DocumentAnalysisResult
from .block3_report import export_estimate_pdf
from .database import Repository
from .models import Block3Estimate, Project, utc_now_iso
from .pdf_pipeline import inspect_pdf, render_analysis_pages


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}".replace(",", " ")


def _number_item(value: float, suffix: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(f"{value:,.2f}{suffix}".replace(",", " ").replace(".", ","))
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return item


def _readonly(text: object) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setToolTip(str(text))
    return item


def _selected_id(table: QTableWidget) -> int | None:
    rows = table.selectionModel().selectedRows()
    if not rows:
        return None
    item = table.item(rows[0].row(), 0)
    return int(item.data(Qt.UserRole)) if item and item.data(Qt.UserRole) is not None else None


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    if len(headers) > 1:
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    return table


def _scenario_context(repository: Repository, estimate: Block3Estimate):
    scenario = repository.get_block2_scenario(estimate.scenario_id)
    block1_row = repository.get_block1_row(scenario.block1_row_id)
    if block1_row is None:
        raise ValueError("Исходная строка блока №1 не найдена")
    research = repository.get_jurisdiction_research(block1_row.country, block1_row.region)
    floors = repository.list_block2_floors(scenario.id)
    calculated = calculate_block2(
        scenario,
        block1_row.goal_code,
        [floors.get(number, ("", 0.0))[1] for number in range(1, scenario.floor_count + 1)],
        research.local_rules if research else None,
    )
    return scenario, block1_row, calculated


def build_summary(repository: Repository, estimate: Block3Estimate) -> EstimateSummary:
    scenario, _row, block2 = _scenario_context(repository, estimate)
    return calculate_estimate(
        estimate,
        scenario,
        block2.building_count,
        block2.gross_floor_area_m2,
        repository.list_materials(estimate.id),
        repository.list_prices(estimate.id),
        repository.list_labor(estimate.id),
        repository.list_resources(estimate.id),
        repository.list_development_costs(estimate.id),
        len(repository.list_block3_documents(estimate.id)),
    )


class DataDialog(QDialog):
    """Small form builder used by manual estimate rows."""

    def __init__(self, title: str, fields: list[tuple], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        self.widgets: dict[str, QWidget] = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for spec in fields:
            name, label, kind, value, *options = spec
            if kind == "text":
                widget = QLineEdit(str(value or ""))
            elif kind == "memo":
                widget = QTextEdit(str(value or ""))
                widget.setMaximumHeight(90)
            elif kind == "float":
                widget = QDoubleSpinBox()
                widget.setRange(0, 1_000_000_000)
                widget.setDecimals(3)
                widget.setValue(float(value or 0))
            elif kind == "int":
                widget = QSpinBox()
                widget.setRange(0, 1_000_000)
                widget.setValue(int(value or 0))
            elif kind == "bool":
                widget = QCheckBox()
                widget.setChecked(bool(value))
            elif kind == "combo":
                widget = QComboBox()
                widget.setEditable(bool(options[1]) if len(options) > 1 else False)
                widget.addItems([str(item) for item in options[0]])
                if str(value) not in options[0] and widget.isEditable():
                    widget.addItem(str(value))
                widget.setCurrentText(str(value))
            else:
                raise ValueError(f"Неизвестный тип поля: {kind}")
            self.widgets[name] = widget
            form.addRow(label, widget)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, widget in self.widgets.items():
            if isinstance(widget, QLineEdit):
                result[name] = widget.text().strip()
            elif isinstance(widget, QTextEdit):
                result[name] = widget.toPlainText().strip()
            elif isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                result[name] = widget.value()
            elif isinstance(widget, QCheckBox):
                result[name] = int(widget.isChecked())
            elif isinstance(widget, QComboBox):
                result[name] = widget.currentText().strip()
        return result


class DocumentAnalysisWorker(QThread):
    status = Signal(str)
    finished_ok = Signal(int, int)
    failed = Signal(str)

    def __init__(
        self, repository: Repository, estimate_id: int, deep: bool, force: bool = False
    ) -> None:
        super().__init__()
        self.repository = repository
        self.estimate_id = estimate_id
        self.deep = deep
        self.force = force

    def run(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(str(exc))

    async def _run(self) -> None:
        documents = self.repository.list_block3_documents(self.estimate_id)
        pending = [
            item for item in documents
            if self.force or item.analysis_status != "ready" or not item.analysis_json
        ]
        if not pending:
            self.finished_ok.emit(0, len(documents))
            return
        def notify(_code: str, message: str) -> None:
            self.status.emit(message)

        async with Block3CodexAnalyzer() as analyzer:
            async def process(document) -> bool:
                self.repository.update_block3_document(
                    document.id, analysis_status="analyzing", error_message=""
                )
                try:
                    self.status.emit(f"Подготовка страниц: {document.original_name}")
                    inspection = inspect_pdf(document.stored_path)
                    pages, images = render_analysis_pages(
                        inspection,
                        self.repository.database_path.parent / "cache" / "pdf_pages",
                        deep=self.deep,
                    )
                    document = self.repository.get_block3_document(document.id)
                    result = await analyzer.analyze_document(
                        document,
                        images,
                        pages,
                        deep=self.deep,
                        status=notify,
                    )
                    self._save_result(document.id, result)
                    return True
                except Exception as exc:  # noqa: BLE001 - isolate document failures
                    self.repository.update_block3_document(
                        document.id, analysis_status="error", error_message=str(exc)
                    )
                    self.status.emit(f"Ошибка в {document.original_name}: {exc}")
                    return False
            outcomes = await asyncio.gather(*(process(document) for document in pending))
        completed = sum(outcomes)
        self.finished_ok.emit(completed, len(pending))

    def _save_result(self, document_id: int, result: DocumentAnalysisResult) -> None:
        self.repository.clear_document_suggestions(self.estimate_id, document_id)
        self.repository.update_block3_document(
            document_id,
            discipline=result.discipline,
            revision=result.revision,
            document_scope=result.document_scope,
            units=result.units,
            scale_status=result.scale_status,
            analysis_status="ready",
            analysis_json=result.model_dump_json(),
            error_message="",
        )
        for item in result.materials:
            self.repository.create_material(
                self.estimate_id,
                work_package=item.work_package,
                description=item.description,
                specification=item.specification,
                quantity=item.quantity,
                unit=item.unit,
                waste_pct=item.waste_pct,
                multiplier=item.multiplier,
                scope=item.scope,
                source_document_id=document_id,
                source_page=item.source_page,
                source_note=item.source_note,
                status="needs_confirmation" if item.requires_confirmation else "draft",
                confidence=item.confidence,
                is_manual=0,
            )
        for item in result.labor:
            self.repository.create_labor(
                self.estimate_id,
                work_package=item.work_package,
                profession=item.profession,
                quantity=item.quantity,
                unit=item.unit,
                norm_hours=item.norm_hours,
                productivity_factor=item.productivity_factor,
                source=f"PDF:{document_id}:{item.source}",
                status="needs_confirmation" if item.requires_confirmation else "draft",
            )


class PriceResearchWorker(QThread):
    status = Signal(str)
    finished_ok = Signal(int, int)
    failed = Signal(str)

    def __init__(
        self,
        repository: Repository,
        estimate_id: int,
        country: str,
        region: str,
        currency: str,
        force: bool,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.estimate_id = estimate_id
        self.country = country
        self.region = region
        self.currency = currency
        self.force = force

    def run(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.failed.emit(str(exc))

    async def _run(self) -> None:
        materials = self.repository.list_materials(self.estimate_id)
        selected_ids = {
            item.material_id for item in self.repository.list_prices(self.estimate_id)
            if item.is_selected
        }
        pending = materials if self.force else [item for item in materials if item.id not in selected_ids]
        if not pending:
            self.finished_ok.emit(0, len(materials))
            return

        def notify(_code: str, message: str) -> None:
            self.status.emit(message)

        async with Block3CodexAnalyzer() as analyzer:
            results = await analyzer.research_prices(
                pending, self.country, self.region, self.currency, notify
            )
        saved = 0
        by_name = {item.description.casefold().strip(): item for item in pending}
        today = date.today().isoformat()
        for result in results:
            for quote in result.quotes:
                key = quote.material_description.casefold().strip()
                material = by_name.get(key)
                if material is None:
                    material = next(
                        (item for name, item in by_name.items() if key in name or name in key),
                        None,
                    )
                if material is None or not quote.url.startswith(("http://", "https://")):
                    continue
                self.repository.create_price(
                    self.estimate_id,
                    material.id,
                    supplier=quote.supplier,
                    product_name=quote.product_name,
                    is_analog=int(quote.is_analog),
                    compatibility_status=quote.compatibility_status,
                    currency=quote.currency,
                    exchange_rate_to_estimate=quote.exchange_rate_to_estimate,
                    fx_observed_at=quote.fx_observed_at,
                    fx_source_url=quote.fx_source_url,
                    unit_price=quote.unit_price,
                    price_quantity=quote.price_quantity,
                    delivery_cost=quote.delivery_cost,
                    duty_cost=quote.duty_cost,
                    tax_cost=quote.tax_cost,
                    url=quote.url,
                    location=quote.location,
                    observed_at=quote.observed_at or today,
                    valid_until=quote.valid_until,
                    availability=quote.availability,
                    is_selected=1 if material.id not in selected_ids else 0,
                    status="verified" if quote.confidence == "high" else "needs_confirmation",
                    notes=quote.notes,
                )
                selected_ids.add(material.id)
                saved += 1
        self.finished_ok.emit(saved, len(pending))


class EstimateWorkspaceDialog(QDialog):
    changed = Signal()

    def __init__(
        self,
        repository: Repository,
        project: Project,
        estimate_id: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.project = project
        self.estimate_id = estimate_id
        self.estimate = repository.get_block3_estimate(estimate_id)
        self.analysis_worker: DocumentAnalysisWorker | None = None
        self.price_worker: PriceResearchWorker | None = None
        self.pdf_document = QPdfDocument(self)
        self.setWindowTitle(f"{project.name} · Проект и смета")
        self.resize(1500, 900)
        self.setModal(True)

        outer = QVBoxLayout(self)
        title_row = QHBoxLayout()
        title = QLabel("Проект и смета")
        title.setObjectName("projectTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.status_label = QLabel("Готово к работе")
        self.status_label.setObjectName("subtitle")
        title_row.addWidget(self.status_label)
        outer.addLayout(title_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._documents_tab(), "1. Проектные PDF")
        self.tabs.addTab(self._materials_tab(), "2. Материалы")
        self.tabs.addTab(self._prices_tab(), "3. Цены и аналоги")
        self.tabs.addTab(self._labor_tab(), "4. Труд и ресурсы")
        self.tabs.addTab(self._summary_tab(), "5. Расчет и проверка")
        outer.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        self.analyze_button = QPushButton("Анализировать PDF")
        self.analyze_button.clicked.connect(lambda: self._analyze_documents(False))
        actions.addWidget(self.analyze_button)
        self.deep_button = QPushButton("Глубокая проверка PDF")
        self.deep_button.clicked.connect(lambda: self._analyze_documents(True))
        actions.addWidget(self.deep_button)
        self.price_button = QPushButton("Обновить цены")
        self.price_button.clicked.connect(lambda: self._research_prices(True))
        actions.addWidget(self.price_button)
        actions.addStretch()
        accept = QPushButton("Зафиксировать ревизию")
        accept.clicked.connect(self._accept_revision)
        actions.addWidget(accept)
        export = QPushButton("Отчет PDF")
        export.setObjectName("primary")
        export.clicked.connect(self._export_pdf)
        actions.addWidget(export)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        outer.addLayout(actions)
        self.reload_all()

    def _documents_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        add = QPushButton("+ Загрузить PDF")
        add.clicked.connect(self._add_documents)
        toolbar.addWidget(add)
        edit = QPushButton("Параметры листа")
        edit.clicked.connect(self._edit_document)
        toolbar.addWidget(edit)
        delete = QPushButton("Удалить из проекта")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete_document)
        toolbar.addWidget(delete)
        toolbar.addStretch()
        hint = QLabel("Копии PDF сохраняются внутри данных проекта; исходный файл не изменяется.")
        hint.setObjectName("subtitle")
        toolbar.addWidget(hint)
        layout.addLayout(toolbar)

        splitter = QSplitter()
        self.documents_table = _table(
            ["ID", "Файл", "Страниц", "Дисциплина", "Ревизия", "Ед.", "Масштаб", "Статус"]
        )
        self.documents_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.documents_table.itemSelectionChanged.connect(self._preview_document)
        splitter.addWidget(self.documents_table)
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_document)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        splitter.addWidget(self.pdf_view)
        splitter.setSizes([850, 600])
        layout.addWidget(splitter, 1)
        return page

    def _materials_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        add = QPushButton("+ Материал")
        add.clicked.connect(self._add_material)
        toolbar.addWidget(add)
        edit = QPushButton("Редактировать")
        edit.clicked.connect(self._edit_material)
        toolbar.addWidget(edit)
        delete = QPushButton("Удалить")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete_material)
        toolbar.addWidget(delete)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.materials_table = _table(
            ["ID", "Материал", "Раздел", "Количество", "Ед.", "Запас", "Источник", "Статус"]
        )
        self.materials_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.materials_table.doubleClicked.connect(self._edit_material)
        layout.addWidget(self.materials_table)
        return page

    def _prices_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        add = QPushButton("+ Цена вручную")
        add.clicked.connect(self._add_price)
        toolbar.addWidget(add)
        edit = QPushButton("Редактировать")
        edit.clicked.connect(self._edit_price)
        toolbar.addWidget(edit)
        select = QPushButton("Использовать в смете")
        select.clicked.connect(self._select_price)
        toolbar.addWidget(select)
        source = QPushButton("Открыть источник")
        source.clicked.connect(self._open_price_source)
        toolbar.addWidget(source)
        delete = QPushButton("Удалить")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete_price)
        toolbar.addWidget(delete)
        toolbar.addStretch()
        self.price_freshness = QLabel()
        self.price_freshness.setObjectName("subtitle")
        toolbar.addWidget(self.price_freshness)
        layout.addLayout(toolbar)
        self.prices_table = _table(
            ["ID", "Материал", "Поставщик / товар", "Цена / база", "Валюта", "Проверено", "Аналог", "Выбрано"]
        )
        self.prices_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.prices_table.doubleClicked.connect(self._edit_price)
        layout.addWidget(self.prices_table)
        return page

    def _labor_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        upper = QHBoxLayout()
        add_labor = QPushButton("+ Профессия / работа")
        add_labor.clicked.connect(self._add_labor)
        upper.addWidget(add_labor)
        edit_labor = QPushButton("Редактировать работу")
        edit_labor.clicked.connect(self._edit_labor)
        upper.addWidget(edit_labor)
        del_labor = QPushButton("Удалить работу")
        del_labor.setObjectName("danger")
        del_labor.clicked.connect(self._delete_labor)
        upper.addWidget(del_labor)
        upper.addStretch()
        layout.addLayout(upper)
        self.labor_table = _table(
            ["ID", "Профессия", "Работа", "Объем", "Норма, ч", "Чел.-ч", "Людей", "Стоимость"]
        )
        self.labor_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.labor_table.doubleClicked.connect(self._edit_labor)
        layout.addWidget(self.labor_table, 1)

        lower = QHBoxLayout()
        add_resource = QPushButton("+ Иной ресурс")
        add_resource.clicked.connect(self._add_resource)
        lower.addWidget(add_resource)
        edit_resource = QPushButton("Редактировать ресурс")
        edit_resource.clicked.connect(self._edit_resource)
        lower.addWidget(edit_resource)
        del_resource = QPushButton("Удалить ресурс")
        del_resource.setObjectName("danger")
        del_resource.clicked.connect(self._delete_resource)
        lower.addWidget(del_resource)
        lower.addStretch()
        layout.addLayout(lower)
        self.resources_table = _table(
            ["ID", "Ресурс", "Категория", "Количество", "Ставка", "Метод", "Стоимость", "Включает"]
        )
        self.resources_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.resources_table.doubleClicked.connect(self._edit_resource)
        layout.addWidget(self.resources_table, 1)
        return page

    def _summary_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        left = QFrame()
        left.setObjectName("card")
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Параметры расчета"))
        form = QFormLayout()
        self.currency = QComboBox()
        self.currency.setEditable(True)
        self.currency.addItems(["USD", "EUR", "RUB", "TZS", "KES", "ZAR", "MZN", "NGN", "GHS"])
        form.addRow("Валюта сметы", self.currency)
        self.stage = QComboBox()
        self.stage.addItems(["preliminary", "concept", "design", "tender", "contract"])
        form.addRow("Стадия", self.stage)
        self.parametric_rate = self._spin(1_000_000_000, 2)
        form.addRow("Укрупненная ставка / м²", self.parametric_rate)
        self.schedule_days = QSpinBox()
        self.schedule_days.setRange(1, 3650)
        form.addRow("Срок строительства, дней", self.schedule_days)
        self.hours_per_day = self._spin(24, 1)
        form.addRow("Часов в смене", self.hours_per_day)
        self.utilization = self._spin(100, 1, "%")
        form.addRow("Полезная загрузка", self.utilization)
        self.overhead = self._spin(100, 1, "%")
        form.addRow("Накладные", self.overhead)
        self.profit = self._spin(100, 1, "%")
        form.addRow("Маржа подрядчика", self.profit)
        self.contingency = self._spin(100, 1, "%")
        form.addRow("Резерв неопределенности", self.contingency)
        self.tax = self._spin(100, 1, "%")
        form.addRow("Налоги в строительной части", self.tax)
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(90)
        form.addRow("Примечания", self.notes)
        left_layout.addLayout(form)
        save = QPushButton("Сохранить параметры")
        save.clicked.connect(self._save_settings)
        left_layout.addWidget(save)
        left_layout.addStretch()
        layout.addWidget(left, 1)

        right = QFrame()
        right.setObjectName("card")
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Прочие затраты девелопера"))
        self.development_table = _table(["ID", "Категория", "Сумма", "Источник / основание"])
        right_layout.addWidget(self.development_table, 1)
        save_dev = QPushButton("Сохранить категории")
        save_dev.clicked.connect(self._save_development_costs)
        right_layout.addWidget(save_dev)
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMinimumHeight(230)
        right_layout.addWidget(self.summary_text)
        layout.addWidget(right, 2)
        return page

    @staticmethod
    def _spin(maximum: float, decimals: int, suffix: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, maximum)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        return spin

    def reload_all(self) -> None:
        self.estimate = self.repository.get_block3_estimate(self.estimate_id)
        self._load_settings()
        self._reload_documents()
        self._reload_materials()
        self._reload_prices()
        self._reload_labor_resources()
        self._reload_development()
        self._reload_summary()
        self.changed.emit()

    def _load_settings(self) -> None:
        estimate = self.estimate
        self.currency.setCurrentText(estimate.currency)
        self.stage.setCurrentText(estimate.estimate_stage)
        self.parametric_rate.setValue(estimate.parametric_rate_per_m2)
        self.schedule_days.setValue(max(1, estimate.schedule_days))
        self.hours_per_day.setValue(estimate.hours_per_day)
        self.utilization.setValue(estimate.utilization_pct)
        self.overhead.setValue(estimate.overhead_pct)
        self.profit.setValue(estimate.profit_pct)
        self.contingency.setValue(estimate.contingency_pct)
        self.tax.setValue(estimate.tax_pct)
        self.notes.setPlainText(estimate.notes)

    def _save_settings(self, quiet: bool = False) -> None:
        self.repository.update_block3_estimate(
            self.estimate_id,
            currency=self.currency.currentText().strip().upper() or "USD",
            estimate_stage=self.stage.currentText(),
            parametric_rate_per_m2=self.parametric_rate.value(),
            schedule_days=self.schedule_days.value(),
            hours_per_day=self.hours_per_day.value(),
            utilization_pct=self.utilization.value(),
            overhead_pct=self.overhead.value(),
            profit_pct=self.profit.value(),
            contingency_pct=self.contingency.value(),
            tax_pct=self.tax.value(),
            notes=self.notes.toPlainText().strip(),
            status="draft",
        )
        self.estimate = self.repository.get_block3_estimate(self.estimate_id)
        self._reload_labor_resources()
        self._reload_summary()
        self.changed.emit()
        if not quiet:
            self.status_label.setText("Параметры сохранены")

    def _reload_documents(self) -> None:
        documents = self.repository.list_block3_documents(self.estimate_id)
        self.documents_table.setRowCount(len(documents))
        for row, document in enumerate(documents):
            id_item = _readonly(document.id)
            id_item.setData(Qt.UserRole, document.id)
            self.documents_table.setItem(row, 0, id_item)
            values = [
                document.original_name,
                document.page_count,
                document.discipline,
                document.revision or "—",
                document.units,
                document.scale_status,
                document.analysis_status,
            ]
            for column, value in enumerate(values, 1):
                self.documents_table.setItem(row, column, _readonly(value))
        if documents and not self.documents_table.selectionModel().selectedRows():
            self.documents_table.selectRow(0)

    def _reload_materials(self) -> None:
        documents = {item.id: item.original_name for item in self.repository.list_block3_documents(self.estimate_id)}
        materials = self.repository.list_materials(self.estimate_id)
        self.materials_table.setRowCount(len(materials))
        for row, material in enumerate(materials):
            id_item = _readonly(material.id)
            id_item.setData(Qt.UserRole, material.id)
            self.materials_table.setItem(row, 0, id_item)
            source = "Вручную" if material.is_manual else documents.get(material.source_document_id or -1, "PDF")
            values = [
                material.description,
                material.work_package,
                f"{material.quantity * material.multiplier:g}",
                material.unit,
                f"{material.waste_pct:g}%",
                source,
                material.status,
            ]
            for column, value in enumerate(values, 1):
                self.materials_table.setItem(row, column, _readonly(value))

    def _reload_prices(self) -> None:
        materials = {item.id: item.description for item in self.repository.list_materials(self.estimate_id)}
        prices = self.repository.list_prices(self.estimate_id)
        self.prices_table.setRowCount(len(prices))
        for row, quote in enumerate(prices):
            id_item = _readonly(quote.id)
            id_item.setData(Qt.UserRole, quote.id)
            self.prices_table.setItem(row, 0, id_item)
            values = [
                materials.get(quote.material_id, "Удаленный материал"),
                f"{quote.supplier} · {quote.product_name}".strip(" ·"),
                f"{quote.unit_price:g} / {quote.price_quantity:g}",
                quote.currency,
                quote.observed_at or "—",
                "Да" if quote.is_analog else "Нет",
                "✓" if quote.is_selected else "",
            ]
            for column, value in enumerate(values, 1):
                self.prices_table.setItem(row, column, _readonly(value))
        summary = build_summary(self.repository, self.estimate)
        self.price_freshness.setText(summary.price_status)

    def _reload_labor_resources(self) -> None:
        labor = self.repository.list_labor(self.estimate_id)
        self.labor_table.setRowCount(len(labor))
        for row, item in enumerate(labor):
            hours = labor_hours(item)
            workers = required_workers(
                hours,
                item.planned_days or self.estimate.schedule_days,
                self.estimate.hours_per_day,
                self.estimate.utilization_pct,
            )
            id_item = _readonly(item.id)
            id_item.setData(Qt.UserRole, item.id)
            self.labor_table.setItem(row, 0, id_item)
            values = [
                item.profession,
                item.work_package,
                f"{item.quantity:g} {item.unit}",
                f"{item.norm_hours:g}",
                f"{hours:,.1f}".replace(",", " "),
                workers,
                _money(hours * item.hourly_rate, self.estimate.currency),
            ]
            for column, value in enumerate(values, 1):
                self.labor_table.setItem(row, column, _readonly(value))

        resources = self.repository.list_resources(self.estimate_id)
        self.resources_table.setRowCount(len(resources))
        for row, item in enumerate(resources):
            id_item = _readonly(item.id)
            id_item.setData(Qt.UserRole, item.id)
            self.resources_table.setItem(row, 0, id_item)
            includes = ", ".join(
                label for enabled, label in (
                    (item.includes_materials, "материалы"),
                    (item.includes_labor, "труд"),
                    (item.includes_equipment, "техника"),
                ) if enabled
            ) or "—"
            values = [
                item.description,
                item.category,
                f"{item.quantity:g} {item.unit}",
                _money(item.unit_rate, self.estimate.currency),
                item.calculation_method,
                _money(resource_cost(item), self.estimate.currency),
                includes,
            ]
            for column, value in enumerate(values, 1):
                self.resources_table.setItem(row, column, _readonly(value))

    def _reload_development(self) -> None:
        costs = self.repository.list_development_costs(self.estimate_id)
        self.development_table.setRowCount(len(costs))
        for row, item in enumerate(costs):
            id_item = _readonly(item.id)
            id_item.setData(Qt.UserRole, item.id)
            self.development_table.setItem(row, 0, id_item)
            self.development_table.setItem(row, 1, _readonly(item.label))
            self.development_table.setItem(row, 2, QTableWidgetItem(f"{item.amount:.2f}"))
            self.development_table.setItem(row, 3, QTableWidgetItem(item.source))

    def _reload_summary(self) -> None:
        summary = build_summary(self.repository, self.estimate)
        issues = "".join(f"<li>{item}</li>" for item in summary.open_issues) or "<li>Критических пробелов нет</li>"
        self.summary_text.setHtml(
            f"<h3>Итог расчета</h3>"
            f"<p><b>Строительная часть:</b> {_money(summary.construction_total, self.estimate.currency)}<br>"
            f"<b>Прочие затраты:</b> {_money(summary.development_total, self.estimate.currency)}<br>"
            f"<b>Полная стоимость:</b> {_money(summary.full_product_total, self.estimate.currency)}<br>"
            f"<b>Трудоемкость:</b> {summary.labor_hours:,.1f} чел.-ч; "
            f"ориентир по составу: {summary.peak_workers} чел.<br>"
            f"<b>Уровень:</b> {summary.estimate_level}; <b>цены:</b> {summary.price_status}</p>"
            f"<h4>Проверить</h4><ul>{issues}</ul>".replace(",", " ")
        )

    def _save_development_costs(self) -> None:
        for row in range(self.development_table.rowCount()):
            item_id = int(self.development_table.item(row, 0).data(Qt.UserRole))
            raw_amount = self.development_table.item(row, 2).text().replace(" ", "").replace(",", ".")
            try:
                amount = max(0.0, float(raw_amount or 0))
            except ValueError:
                QMessageBox.warning(self, "Некорректная сумма", f"Строка {row + 1}: укажите число.")
                return
            source = self.development_table.item(row, 3).text().strip()
            self.repository.update_development_cost(item_id, amount=amount, source=source)
        self.status_label.setText("Категории затрат сохранены")
        self._reload_summary()
        self.changed.emit()

    def _add_documents(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Загрузить листы проекта", "", "PDF (*.pdf)")
        added = 0
        errors: list[str] = []
        for path in paths:
            try:
                inspection = inspect_pdf(path)
                before = len(self.repository.list_block3_documents(self.estimate_id))
                self.repository.add_block3_document(
                    self.estimate_id,
                    path,
                    sha256=inspection.sha256,
                    size_bytes=inspection.size_bytes,
                    page_count=inspection.page_count,
                    extracted_text=inspection.extracted_text,
                )
                after = len(self.repository.list_block3_documents(self.estimate_id))
                added += int(after > before)
            except Exception as exc:  # noqa: BLE001 - one bad user file must not stop batch
                errors.append(f"{Path(path).name}: {exc}")
        self.reload_all()
        if paths:
            message = f"Добавлено новых PDF: {added}. Дубликаты определяются по содержимому."
            if errors:
                message += "\n\nНе загружено:\n" + "\n".join(errors)
            QMessageBox.information(self, "Загрузка проекта", message)

    def _preview_document(self) -> None:
        document_id = _selected_id(self.documents_table)
        if document_id is None:
            return
        document = self.repository.get_block3_document(document_id)
        self.pdf_document.load(document.stored_path)

    def _edit_document(self) -> None:
        document_id = _selected_id(self.documents_table)
        if document_id is None:
            return
        item = self.repository.get_block3_document(document_id)
        dialog = DataDialog("Параметры проектного PDF", [
            ("discipline", "Дисциплина", "text", item.discipline),
            ("revision", "Ревизия", "text", item.revision),
            ("document_scope", "Объем документа", "text", item.document_scope),
            ("units", "Единицы", "text", item.units),
            ("scale_status", "Масштаб / измеримость", "text", item.scale_status),
        ], self)
        if dialog.exec() == QDialog.Accepted:
            self.repository.update_block3_document(document_id, **dialog.values())
            self.reload_all()

    def _delete_document(self) -> None:
        document_id = _selected_id(self.documents_table)
        if document_id is None:
            return
        if QMessageBox.question(
            self, "Удалить PDF?", "Документ и автоматически извлеченные из него строки будут удалены из сметы.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self.repository.clear_document_suggestions(self.estimate_id, document_id)
            self.repository.delete_block3_document(document_id)
            self.reload_all()

    def _material_fields(self, item=None) -> list[tuple]:
        get = lambda name, default: getattr(item, name, default) if item else default
        return [
            ("work_package", "Раздел работ", "combo", get("work_package", "Прочее"), WORK_PACKAGES),
            ("description", "Наименование*", "text", get("description", "")),
            ("specification", "Спецификация", "text", get("specification", "")),
            ("quantity", "Чистое количество", "float", get("quantity", 0)),
            ("unit", "Единица", "combo", get("unit", "шт"), UNITS, True),
            ("waste_pct", "Запас / отходы, %", "float", get("waste_pct", 0)),
            ("package_size", "Размер упаковки", "float", get("package_size", 1)),
            ("multiplier", "Множитель типовых объектов", "float", get("multiplier", 1)),
            ("scope", "Область применения", "text", get("scope", "Весь проект")),
            ("status", "Статус", "combo", get("status", "confirmed"), ["draft", "needs_confirmation", "confirmed"]),
            ("notes", "Примечания", "memo", get("notes", "")),
        ]

    def _add_material(self) -> None:
        dialog = DataDialog("Добавить материал", self._material_fields(), self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["description"]:
            QMessageBox.warning(self, "Нет наименования", "Укажите материал.")
            return
        self.repository.create_material(self.estimate_id, **values, is_manual=1, confidence="high")
        self.reload_all()

    def _edit_material(self, *_args: object) -> None:
        item_id = _selected_id(self.materials_table)
        if item_id is None:
            return
        item = next(row for row in self.repository.list_materials(self.estimate_id) if row.id == item_id)
        dialog = DataDialog("Редактировать материал", self._material_fields(item), self)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            if not values["description"]:
                QMessageBox.warning(self, "Нет наименования", "Укажите материал.")
                return
            self.repository.update_material(item_id, **values)
            self.reload_all()

    def _delete_material(self) -> None:
        item_id = _selected_id(self.materials_table)
        if item_id is not None and QMessageBox.question(
            self, "Удалить материал?", "Связанные с ним цены также будут удалены.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self.repository.delete_material(item_id)
            self.reload_all()

    def _price_fields(self, item=None, material_id: int | None = None) -> list[tuple]:
        get = lambda name, default: getattr(item, name, default) if item else default
        materials = self.repository.list_materials(self.estimate_id)
        material_options = [f"{row.id}: {row.description}" for row in materials]
        selected_material = material_id or get("material_id", materials[0].id if materials else 0)
        material_value = next((label for label in material_options if label.startswith(f"{selected_material}:")), "")
        return [
            ("material", "Материал*", "combo", material_value, material_options),
            ("supplier", "Поставщик", "text", get("supplier", "")),
            ("product_name", "Товар / аналог", "text", get("product_name", "")),
            ("is_analog", "Это аналог", "bool", get("is_analog", 0)),
            ("compatibility_status", "Сопоставимость", "combo", get("compatibility_status", "exact"), ["exact", "equivalent", "conditional", "not_comparable"]),
            ("currency", "Валюта", "text", get("currency", self.estimate.currency)),
            ("exchange_rate_to_estimate", f"Курс: 1 ед. цены = X {self.estimate.currency}", "float", get("exchange_rate_to_estimate", 1)),
            ("fx_observed_at", "Дата курса (ГГГГ-ММ-ДД)", "text", get("fx_observed_at", date.today().isoformat())),
            ("fx_source_url", "URL источника курса", "text", get("fx_source_url", "")),
            ("unit_price", "Цена за ценовую единицу", "float", get("unit_price", 0)),
            ("price_quantity", "Количество в ценовой единице", "float", get("price_quantity", 1)),
            ("delivery_cost", "Доставка на весь объем", "float", get("delivery_cost", 0)),
            ("duty_cost", "Пошлина на весь объем", "float", get("duty_cost", 0)),
            ("tax_cost", "Налоги на весь объем", "float", get("tax_cost", 0)),
            ("url", "URL источника", "text", get("url", "")),
            ("location", "Город / регион поставщика", "text", get("location", "")),
            ("observed_at", "Проверено (ГГГГ-ММ-ДД)", "text", get("observed_at", date.today().isoformat())),
            ("valid_until", "Действительно до", "text", get("valid_until", (date.today() + timedelta(days=30)).isoformat())),
            ("availability", "Наличие", "text", get("availability", "Требует подтверждения")),
            ("status", "Статус", "combo", get("status", "manual"), ["manual", "verified", "needs_confirmation"]),
            ("notes", "Примечания", "memo", get("notes", "")),
        ]

    @staticmethod
    def _extract_material(values: dict[str, object]) -> int:
        label = str(values.pop("material"))
        return int(label.split(":", 1)[0])

    def _add_price(self) -> None:
        if not self.repository.list_materials(self.estimate_id):
            QMessageBox.information(self, "Нет материалов", "Сначала добавьте материал.")
            return
        dialog = DataDialog("Добавить цену", self._price_fields(), self)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            material_id = self._extract_material(values)
            self.repository.create_price(self.estimate_id, material_id, **values, is_selected=1)
            self.reload_all()

    def _edit_price(self, *_args: object) -> None:
        quote_id = _selected_id(self.prices_table)
        if quote_id is None:
            return
        quote = self.repository.get_price(quote_id)
        dialog = DataDialog("Редактировать цену", self._price_fields(quote), self)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            material_id = self._extract_material(values)
            if material_id != quote.material_id:
                self.repository.delete_price(quote_id)
                self.repository.create_price(self.estimate_id, material_id, **values, is_selected=1)
            else:
                self.repository.update_price(quote_id, **values)
            self.reload_all()

    def _select_price(self) -> None:
        quote_id = _selected_id(self.prices_table)
        if quote_id is not None:
            self.repository.select_price(quote_id)
            self.reload_all()

    def _open_price_source(self) -> None:
        quote_id = _selected_id(self.prices_table)
        if quote_id is None:
            return
        url = self.repository.get_price(quote_id).url
        if url.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(url))
        else:
            QMessageBox.information(self, "Нет ссылки", "Для выбранной цены URL источника не заполнен.")

    def _delete_price(self) -> None:
        quote_id = _selected_id(self.prices_table)
        if quote_id is not None:
            self.repository.delete_price(quote_id)
            self.reload_all()

    def _labor_fields(self, item=None) -> list[tuple]:
        get = lambda name, default: getattr(item, name, default) if item else default
        return [
            ("work_package", "Раздел работ", "combo", get("work_package", "Прочее"), WORK_PACKAGES),
            ("profession", "Профессия*", "text", get("profession", "")),
            ("quantity", "Объем работ", "float", get("quantity", 0)),
            ("unit", "Единица объема", "combo", get("unit", "шт"), UNITS, True),
            ("norm_hours", "Норма, чел.-ч / ед.", "float", get("norm_hours", 0)),
            ("productivity_factor", "Коэффициент условий", "float", get("productivity_factor", 1)),
            ("planned_days", "Плановый срок работы, дней", "int", get("planned_days", 0)),
            ("hourly_rate", f"Ставка, {self.estimate.currency}/ч", "float", get("hourly_rate", 0)),
            ("source", "Источник нормы / ставки", "text", get("source", "")),
            ("status", "Статус", "combo", get("status", "confirmed"), ["draft", "needs_confirmation", "confirmed"]),
            ("notes", "Примечания", "memo", get("notes", "")),
        ]

    def _add_labor(self) -> None:
        dialog = DataDialog("Добавить работу", self._labor_fields(), self)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            if not values["profession"]:
                QMessageBox.warning(self, "Нет профессии", "Укажите профессию.")
                return
            self.repository.create_labor(self.estimate_id, **values)
            self.reload_all()

    def _edit_labor(self, *_args: object) -> None:
        item_id = _selected_id(self.labor_table)
        if item_id is None:
            return
        item = next(row for row in self.repository.list_labor(self.estimate_id) if row.id == item_id)
        dialog = DataDialog("Редактировать работу", self._labor_fields(item), self)
        if dialog.exec() == QDialog.Accepted:
            self.repository.update_labor(item_id, **dialog.values())
            self.reload_all()

    def _delete_labor(self) -> None:
        item_id = _selected_id(self.labor_table)
        if item_id is not None:
            self.repository.delete_labor(item_id)
            self.reload_all()

    def _resource_fields(self, item=None) -> list[tuple]:
        get = lambda name, default: getattr(item, name, default) if item else default
        return [
            ("category", "Категория", "combo", get("category", "Техника"), ["Техника", "Субподряд", "Временная инфраструктура", "Логистика", "Энергия", "Вода", "Прочее"], True),
            ("description", "Ресурс*", "text", get("description", "")),
            ("calculation_method", "Метод расчета", "combo", get("calculation_method", "quantity"), ["quantity", "time", "quantity_time", "fixed"]),
            ("quantity", "Количество", "float", get("quantity", 0)),
            ("unit", "Единица", "combo", get("unit", "шт"), UNITS, True),
            ("unit_rate", f"Ставка, {self.estimate.currency}", "float", get("unit_rate", 0)),
            ("duration", "Длительность", "float", get("duration", 0)),
            ("includes_materials", "Включает материалы", "bool", get("includes_materials", 0)),
            ("includes_labor", "Включает труд", "bool", get("includes_labor", 0)),
            ("includes_equipment", "Включает технику", "bool", get("includes_equipment", 0)),
            ("source", "Источник ставки", "text", get("source", "")),
            ("status", "Статус", "combo", get("status", "confirmed"), ["draft", "needs_confirmation", "confirmed"]),
            ("notes", "Примечания", "memo", get("notes", "")),
        ]

    def _add_resource(self) -> None:
        dialog = DataDialog("Добавить иной ресурс", self._resource_fields(), self)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            if not values["description"]:
                QMessageBox.warning(self, "Нет ресурса", "Укажите ресурс.")
                return
            self.repository.create_resource(self.estimate_id, **values)
            self.reload_all()

    def _edit_resource(self, *_args: object) -> None:
        item_id = _selected_id(self.resources_table)
        if item_id is None:
            return
        item = next(row for row in self.repository.list_resources(self.estimate_id) if row.id == item_id)
        dialog = DataDialog("Редактировать ресурс", self._resource_fields(item), self)
        if dialog.exec() == QDialog.Accepted:
            self.repository.update_resource(item_id, **dialog.values())
            self.reload_all()

    def _delete_resource(self) -> None:
        item_id = _selected_id(self.resources_table)
        if item_id is not None:
            self.repository.delete_resource(item_id)
            self.reload_all()

    def _set_busy(self, busy: bool) -> None:
        self.analyze_button.setEnabled(not busy)
        self.deep_button.setEnabled(not busy)
        self.price_button.setEnabled(not busy)
        self.tabs.setEnabled(not busy)

    def _analyze_documents(self, deep: bool) -> None:
        documents = self.repository.list_block3_documents(self.estimate_id)
        if not documents:
            QMessageBox.information(self, "Нет PDF", "Сначала загрузите проектные PDF.")
            return
        if not codex_sdk_available():
            QMessageBox.warning(self, "Codex недоступен", "Подключение Codex не установлено.")
            return
        self._set_busy(True)
        self.status_label.setText("Подготовка анализа…")
        self.analysis_worker = DocumentAnalysisWorker(
            self.repository, self.estimate_id, deep=deep, force=deep
        )
        self.analysis_worker.status.connect(self.status_label.setText)
        self.analysis_worker.finished_ok.connect(self._analysis_finished)
        self.analysis_worker.failed.connect(self._worker_failed)
        self.analysis_worker.start()

    def _analysis_finished(self, completed: int, total: int) -> None:
        worker = self.analysis_worker
        self.analysis_worker = None
        if worker:
            worker.deleteLater()
        self._set_busy(False)
        self.reload_all()
        self.status_label.setText(f"Анализ PDF завершен: {completed} из {total}")
        if completed:
            self.tabs.setCurrentIndex(1)

    def _research_prices(self, force: bool) -> None:
        if not self.repository.list_materials(self.estimate_id):
            QMessageBox.information(self, "Нет материалов", "Сначала загрузите и проанализируйте PDF или добавьте материалы вручную.")
            return
        if not codex_sdk_available():
            QMessageBox.warning(self, "Codex недоступен", "Подключение Codex не установлено.")
            return
        self._save_settings(quiet=True)
        _scenario, row, _calculated = _scenario_context(self.repository, self.estimate)
        self._set_busy(True)
        self.status_label.setText("Подготовка поиска цен…")
        self.price_worker = PriceResearchWorker(
            self.repository,
            self.estimate_id,
            row.country,
            row.region,
            self.estimate.currency,
            force,
        )
        self.price_worker.status.connect(self.status_label.setText)
        self.price_worker.finished_ok.connect(self._prices_finished)
        self.price_worker.failed.connect(self._worker_failed)
        self.price_worker.start()

    def _prices_finished(self, saved: int, materials: int) -> None:
        worker = self.price_worker
        self.price_worker = None
        if worker:
            worker.deleteLater()
        self._set_busy(False)
        self.reload_all()
        self.tabs.setCurrentIndex(2)
        self.status_label.setText(f"Сохранено предложений: {saved}; проверено материалов: {materials}")

    def _worker_failed(self, message: str) -> None:
        for worker_name in ("analysis_worker", "price_worker"):
            worker = getattr(self, worker_name)
            if worker:
                worker.deleteLater()
                setattr(self, worker_name, None)
        self._set_busy(False)
        self.reload_all()
        self.status_label.setText("Операция завершилась с ошибкой")
        QMessageBox.warning(self, "Ошибка блока №3", message)

    def _revision_payload(self) -> dict[str, object]:
        summary = build_summary(self.repository, self.estimate)
        scenario, row, calculated = _scenario_context(self.repository, self.estimate)
        return {
            "accepted_at": utc_now_iso(),
            "project": asdict(self.project),
            "location": {"country": row.country, "region": row.region, "goal": row.goal_label},
            "scenario": asdict(scenario),
            "block2_calculation": asdict(calculated),
            "estimate": asdict(self.estimate),
            "summary": summary.to_payload(),
            "documents": [asdict(item) for item in self.repository.list_block3_documents(self.estimate_id)],
            "materials": [asdict(item) for item in self.repository.list_materials(self.estimate_id)],
            "prices": [asdict(item) for item in self.repository.list_prices(self.estimate_id)],
            "labor": [asdict(item) for item in self.repository.list_labor(self.estimate_id)],
            "resources": [asdict(item) for item in self.repository.list_resources(self.estimate_id)],
            "development_costs": [asdict(item) for item in self.repository.list_development_costs(self.estimate_id)],
        }

    def _accept_revision(self) -> None:
        self._save_settings(quiet=True)
        self._save_development_costs()
        summary = build_summary(self.repository, self.estimate)
        if summary.open_issues:
            text = "В смете остались предупреждения:\n\n" + "\n".join(f"• {item}" for item in summary.open_issues)
            text += "\n\nВсе равно зафиксировать текущую ревизию?"
            if QMessageBox.question(
                self, "Зафиксировать с предупреждениями?", text,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return
        revision = self.repository.save_estimate_revision(self.estimate_id, self._revision_payload())
        self.estimate = self.repository.get_block3_estimate(self.estimate_id)
        self.status_label.setText(f"Зафиксирована ревизия №{revision.version}")
        self.changed.emit()

    def _export_pdf(self) -> None:
        self._save_settings(quiet=True)
        self._save_development_costs()
        default_name = f"{self.project.name}_смета.pdf".replace("/", "_").replace("\\", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчет", default_name, "PDF (*.pdf)")
        if not path:
            return
        if not path.casefold().endswith(".pdf"):
            path += ".pdf"
        try:
            scenario, row, _calculated = _scenario_context(self.repository, self.estimate)
            target = export_estimate_pdf(
                path,
                project=self.project,
                scenario_label=scenario.name,
                location_label=f"{row.country} · {row.region} · {row.goal_label}",
                estimate=self.estimate,
                summary=build_summary(self.repository, self.estimate),
                documents=self.repository.list_block3_documents(self.estimate_id),
                materials=self.repository.list_materials(self.estimate_id),
                quotes=self.repository.list_prices(self.estimate_id),
                labor=self.repository.list_labor(self.estimate_id),
                resources=self.repository.list_resources(self.estimate_id),
                development_costs=self.repository.list_development_costs(self.estimate_id),
            )
        except Exception as exc:  # noqa: BLE001 - report boundary
            QMessageBox.warning(self, "Не удалось создать PDF", str(exc))
            return
        self.status_label.setText(f"Отчет сохранен: {target.name}")
        if QMessageBox.question(
            self, "Отчет готов", "PDF сформирован. Открыть его?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        ) == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def reject(self) -> None:
        if self.analysis_worker or self.price_worker:
            QMessageBox.information(self, "Операция выполняется", "Дождитесь завершения анализа или поиска цен.")
            return
        super().reject()

    def accept(self) -> None:
        if self.analysis_worker or self.price_worker:
            QMessageBox.information(self, "Операция выполняется", "Дождитесь завершения анализа или поиска цен.")
            return
        super().accept()


class Block3Page(QWidget):
    back_requested = Signal()

    def __init__(
        self, repository: Repository, project: Project, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.project = project
        self.estimates: list[Block3Estimate] = []
        self.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)

        header = QHBoxLayout()
        back = QPushButton("← Блок № 2")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        title = QLabel(f"{project.name} · Блок № 3")
        title.setObjectName("projectTitle")
        header.addWidget(title)
        header.addStretch()
        outer.addLayout(header)

        note = QLabel(
            "Лаконичный итог по выбранному сценарию. Подробная загрузка проекта, ведомость "
            "материалов, актуальные цены, трудоемкость и ручные ресурсы находятся в отдельном окне «Проект и смета»."
        )
        note.setWordWrap(True)
        note.setObjectName("subtitle")
        outer.addWidget(note)

        selector = QHBoxLayout()
        selector.addWidget(QLabel("Сценарий блока №2:"))
        self.scenario_combo = QComboBox()
        self.scenario_combo.currentIndexChanged.connect(self.reload_summary)
        selector.addWidget(self.scenario_combo, 1)
        self.calculate_button = QPushButton("Расчет блока №3")
        self.calculate_button.setObjectName("primary")
        self.calculate_button.clicked.connect(self._calculate)
        selector.addWidget(self.calculate_button)
        workspace = QPushButton("Проект и смета…")
        workspace.clicked.connect(self._open_workspace)
        selector.addWidget(workspace)
        outer.addLayout(selector)

        cards = QHBoxLayout()
        self.construction_card = self._metric_card("Строительная часть")
        self.full_card = self._metric_card("Полная стоимость продукта")
        self.labor_card = self._metric_card("Трудоемкость / состав")
        self.price_card = self._metric_card("Актуальность цен")
        for card, _label in (self.construction_card, self.full_card, self.labor_card, self.price_card):
            cards.addWidget(card)
        outer.addLayout(cards)

        self.category_table = _table(["Категория", "Сумма", "Источник / основание"])
        self.category_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        outer.addWidget(self.category_table, 1)
        self.issues_label = QLabel()
        self.issues_label.setWordWrap(True)
        outer.addWidget(self.issues_label)
        self.status_label = QLabel()
        self.status_label.setObjectName("subtitle")
        outer.addWidget(self.status_label)
        self.reload()

    @staticmethod
    def _metric_card(title: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        caption = QLabel(title)
        caption.setObjectName("subtitle")
        value = QLabel("—")
        value.setStyleSheet("font-size: 15pt; font-weight: 650; color: #153a31;")
        value.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(value)
        return card, value

    def reload(self) -> None:
        selected_id = self.current_estimate_id()
        self.repository.ensure_block3_estimates(self.project.id)
        self.estimates = self.repository.list_block3_estimates(self.project.id)
        self.scenario_combo.blockSignals(True)
        self.scenario_combo.clear()
        for estimate in self.estimates:
            scenario, row, _calculated = _scenario_context(self.repository, estimate)
            self.scenario_combo.addItem(
                f"{scenario.name} · {row.country}, {row.region} · {row.goal_label}", estimate.id
            )
        if selected_id is not None:
            index = self.scenario_combo.findData(selected_id)
            self.scenario_combo.setCurrentIndex(max(0, index))
        self.scenario_combo.blockSignals(False)
        self.calculate_button.setEnabled(bool(self.estimates))
        self.reload_summary()

    def current_estimate_id(self) -> int | None:
        value = self.scenario_combo.currentData()
        return int(value) if value is not None else None

    def reload_summary(self, *_args: object) -> None:
        estimate_id = self.current_estimate_id()
        if estimate_id is None:
            for _card, label in (self.construction_card, self.full_card, self.labor_card, self.price_card):
                label.setText("—")
            self.category_table.setRowCount(0)
            self.issues_label.setText("В блоке №2 пока нет сценариев. Создайте сценарий и заполните его площади.")
            self.status_label.setText("")
            return
        estimate = self.repository.get_block3_estimate(estimate_id)
        summary = build_summary(self.repository, estimate)
        self.construction_card[1].setText(_money(summary.construction_total, estimate.currency))
        self.full_card[1].setText(_money(summary.full_product_total, estimate.currency))
        self.labor_card[1].setText(f"{summary.labor_hours:,.1f} чел.-ч · {summary.peak_workers} чел.".replace(",", " "))
        self.price_card[1].setText(summary.price_status)
        costs = self.repository.list_development_costs(estimate_id)
        construction_row = len(costs)
        self.category_table.setRowCount(len(costs) + 1)
        for row, item in enumerate(costs):
            self.category_table.setItem(row, 0, _readonly(item.label))
            self.category_table.setItem(row, 1, _readonly(_money(item.amount, estimate.currency)))
            self.category_table.setItem(row, 2, _readonly(item.source or "Не заполнено"))
        self.category_table.setItem(construction_row, 0, _readonly("Строительная часть"))
        self.category_table.setItem(construction_row, 1, _readonly(_money(summary.construction_total, estimate.currency)))
        self.category_table.setItem(construction_row, 2, _readonly("Материалы + труд + ресурсы либо ставка на м²"))
        self.issues_label.setText(
            "Проверить: " + " · ".join(summary.open_issues)
            if summary.open_issues else "Проверка: критические незакрытые вопросы не выявлены."
        )
        revisions = self.repository.list_estimate_revisions(estimate_id)
        revision_text = f" · Зафиксированных ревизий: {len(revisions)}" if revisions else ""
        self.status_label.setText(f"Уровень сметы: {summary.estimate_level}{revision_text}")

    def _calculate(self) -> None:
        estimate_id = self.current_estimate_id()
        if estimate_id is None:
            return
        self.repository.update_block3_estimate(estimate_id, status="calculated")
        self.reload_summary()
        self.status_label.setText("Расчет блока №3 выполнен локально и сохранен")

    def _open_workspace(self) -> None:
        estimate_id = self.current_estimate_id()
        if estimate_id is None:
            QMessageBox.information(self, "Нет сценария", "Сначала добавьте сценарий в блоке №2.")
            return
        dialog = EstimateWorkspaceDialog(self.repository, self.project, estimate_id, self)
        dialog.changed.connect(self.reload_summary)
        dialog.exec()
        self.reload()
