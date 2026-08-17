from __future__ import annotations

import asyncio
import html
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .analysis import AsyncCodexAnalyzer, assemble_block1_analysis, codex_sdk_available
from .block2_ui import Block2Page
from .block3_ui import Block3Page
from .constants import (
    AFRICAN_COUNTRIES_RU,
    MAX_BLOCK1_ROWS,
    PROJECT_GOALS,
    STATUS_LABELS,
)
from .database import Repository
from .models import Block1Analysis, Block1Row, LegalFinding, Project, jurisdiction_key


APP_STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 10pt; color: #17202a; }
QMainWindow, QWidget#root { background: #f4f6f8; }
QFrame#card { background: white; border: 1px solid #dfe5ea; border-radius: 10px; }
QLabel#title { font-size: 22pt; font-weight: 700; color: #153a31; }
QLabel#subtitle { color: #5c6873; }
QLabel#projectTitle { font-size: 17pt; font-weight: 650; color: #153a31; }
QPushButton, QToolButton { min-height: 34px; padding: 0 14px; border-radius: 6px;
  border: 1px solid #b7c2ca; background: white; }
QPushButton:hover, QToolButton:hover { border-color: #2d7c68; background: #f1faf7; }
QPushButton#primary, QToolButton#primary { background: #196c57; color: white;
  border-color: #196c57; font-weight: 600; }
QPushButton#primary:hover, QToolButton#primary:hover { background: #125845; }
QPushButton#danger { color: #9c2f2f; }
QLineEdit, QComboBox { min-height: 32px; padding: 0 7px; border-radius: 5px;
  border: 1px solid #cbd3d9; background: white; }
QTableWidget { background: white; border: 1px solid #dfe5ea; gridline-color: #e8ecef;
  selection-background-color: #dff1eb; selection-color: #17202a; }
QHeaderView::section { background: #e9f1ee; color: #173e34; border: none;
  border-right: 1px solid #d4ded9; border-bottom: 1px solid #c8d5d0;
  padding: 8px 6px; font-weight: 600; }
QTextBrowser { background: white; border: 1px solid #dfe5ea; border-radius: 8px; padding: 8px; }
"""


class ProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Создать проект")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Например: Zanzibar Villas")
        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText("Необязательно")
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("Краткое описание, необязательно")
        form.addRow("Название проекта*", self.name_edit)
        form.addRow("Заказчик / инвестор", self.client_edit)
        form.addRow("Описание", self.description_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Создать")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Не указано название", "Введите название проекта.")
            return
        self.accept()


class StartPage(QWidget):
    project_opened = Signal(int)

    def __init__(self, repository: Repository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(42, 32, 42, 32)
        outer.setSpacing(18)

        title = QLabel("African Villas")
        title.setObjectName("title")
        subtitle = QLabel("Проекты анализа и бизнес-планирования · блоки № 1–3")
        subtitle.setObjectName("subtitle")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        actions = QHBoxLayout()
        self.create_button = QToolButton()
        self.create_button.setObjectName("primary")
        self.create_button.setText("Создать проект")
        self.create_button.setPopupMode(QToolButton.InstantPopup)
        create_menu = QMenu(self.create_button)
        new_action = QAction("Новый проект", create_menu)
        new_action.triggered.connect(self._create_project)
        create_menu.addAction(new_action)
        copy_action = QAction("Создать копию существующего — позже", create_menu)
        copy_action.setEnabled(False)
        create_menu.addAction(copy_action)
        self.create_button.setMenu(create_menu)
        actions.addWidget(self.create_button)

        self.open_button = QPushButton("Открыть выбранный")
        self.open_button.clicked.connect(self._open_selected)
        actions.addWidget(self.open_button)
        actions.addStretch()
        outer.addLayout(actions)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(QLabel("Сохранённые проекты"))
        self.projects_table = QTableWidget(0, 4)
        self.projects_table.setHorizontalHeaderLabels(
            ["Название", "Заказчик", "Последнее изменение", "Блок № 1"]
        )
        self.projects_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.projects_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.projects_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.projects_table.verticalHeader().setVisible(False)
        self.projects_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.projects_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.projects_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.projects_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.projects_table.doubleClicked.connect(self._open_selected)
        card_layout.addWidget(self.projects_table)
        outer.addWidget(card, 1)
        self.refresh()

    def refresh(self) -> None:
        projects = self.repository.list_projects()
        self.projects_table.setRowCount(len(projects))
        for row_index, project in enumerate(projects):
            name_item = QTableWidgetItem(project.name)
            name_item.setData(Qt.UserRole, project.id)
            self.projects_table.setItem(row_index, 0, name_item)
            self.projects_table.setItem(row_index, 1, QTableWidgetItem(project.client_name or "—"))
            self.projects_table.setItem(row_index, 2, QTableWidgetItem(project.updated_at))
            ready, total = self.repository.project_progress(project.id)
            progress = "Нет строк" if total == 0 else f"Готово {ready} из {total}"
            self.projects_table.setItem(row_index, 3, QTableWidgetItem(progress))
        self.open_button.setEnabled(bool(projects))
        if projects and self.projects_table.currentRow() < 0:
            self.projects_table.selectRow(0)

    def _create_project(self) -> None:
        dialog = ProjectDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        project = self.repository.create_project(
            dialog.name_edit.text(),
            dialog.description_edit.text(),
            dialog.client_edit.text(),
        )
        self.refresh()
        self.project_opened.emit(project.id)

    def _open_selected(self, *_args: object) -> None:
        selected = self.projects_table.selectionModel().selectedRows()
        visual_row = selected[0].row() if selected else self.projects_table.currentRow()
        if visual_row < 0:
            QMessageBox.information(self, "Проект не выбран", "Выберите проект из списка.")
            return
        item = self.projects_table.item(visual_row, 0)
        self.project_opened.emit(int(item.data(Qt.UserRole)))


class AnalysisWorker(QThread):
    row_status = Signal(int, str, str)
    row_finished = Signal(int)
    row_failed = Signal(int, str)
    batch_finished = Signal(int, int, bool)

    def __init__(
        self,
        repository: Repository,
        rows: list[Block1Row],
        mode: str,
        force_refresh_ids: set[int] | None = None,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.rows = rows
        self.mode = "standard" if mode == "refresh" else mode
        self.force_refresh_ids = force_refresh_ids or set()
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        success_count = 0
        failure_count = 0
        stopped = False

        groups: dict[str, list[Block1Row]] = {}
        for row in self.rows:
            groups.setdefault(jurisdiction_key(row.country, row.region), []).append(row)

        research_groups: list[list[Block1Row]] = []
        for group_rows in groups.values():
            if self._stop_requested:
                stopped = True
                for row in group_rows:
                    self.repository.set_row_status(row.id, "stopped")
                    self.row_status.emit(row.id, "stopped", "Расчёт остановлен")
                continue
            representative = group_rows[0]
            force = any(row.id in self.force_refresh_ids for row in group_rows)
            cached = None if force else self.repository.get_jurisdiction_research(
                representative.country, representative.region
            )
            if cached is None:
                research_groups.append(group_rows)
                continue
            for row in group_rows:
                self.row_status.emit(
                    row.id, "cached", f"Используется кэш от {cached.checked_at}"
                )
                self.repository.save_analysis(
                    row.id, assemble_block1_analysis(cached, row.goal_label)
                )
                success_count += 1
                self.row_finished.emit(row.id)

        semaphore = asyncio.Semaphore(2)

        async def research_group(
            analyzer: AsyncCodexAnalyzer, group_rows: list[Block1Row]
        ) -> tuple[int, int]:
            if self._stop_requested:
                for row in group_rows:
                    self.repository.set_row_status(row.id, "stopped")
                    self.row_status.emit(row.id, "stopped", "Расчёт остановлен")
                return 0, 0
            async with semaphore:
                representative = group_rows[0]
                for row in group_rows:
                    self.repository.set_row_status(row.id, "queued")
                    self.row_status.emit(row.id, "queued", "Юрисдикция добавлена в очередь")

                def update(status: str, message: str) -> None:
                    for member in group_rows:
                        self.repository.set_row_status(member.id, status)
                        self.row_status.emit(member.id, status, message)

                try:
                    research = await analyzer.analyze_jurisdiction(
                        representative.country,
                        representative.region,
                        [row.goal_label for row in group_rows],
                        self.mode,
                        update,
                    )
                    self.repository.save_jurisdiction_research(research)
                    for row in group_rows:
                        self.repository.save_analysis(
                            row.id, assemble_block1_analysis(research, row.goal_label)
                        )
                        self.row_finished.emit(row.id)
                    return len(group_rows), 0
                except Exception as exc:  # noqa: BLE001 - UI boundary
                    message = str(exc).strip() or exc.__class__.__name__
                    for row in group_rows:
                        self.repository.set_row_status(row.id, "error", message)
                        self.row_failed.emit(row.id, message)
                    return 0, len(group_rows)

        try:
            if research_groups:
                async with AsyncCodexAnalyzer() as analyzer:
                    outcomes = await asyncio.gather(
                        *(research_group(analyzer, group) for group in research_groups)
                    )
                success_count += sum(result[0] for result in outcomes)
                failure_count += sum(result[1] for result in outcomes)
        except Exception as exc:  # noqa: BLE001 - SDK startup boundary
            message = str(exc).strip() or exc.__class__.__name__
            pending_ids = {row.id for group in research_groups for row in group}
            for row in self.rows:
                if row.id not in pending_ids:
                    continue
                self.repository.set_row_status(row.id, "error", message)
                self.row_failed.emit(row.id, message)
                failure_count += 1
        stopped = stopped or self._stop_requested
        self.batch_finished.emit(success_count, failure_count, stopped)


class Block1Page(QWidget):
    back_requested = Signal()
    next_requested = Signal()

    COL_NUMBER = 0
    COL_COUNTRY = 1
    COL_REGION = 2
    COL_MAP = 3
    COL_GOAL = 4
    COL_FRESHNESS = 5
    COL_LAND = 6
    COL_ENTITY = 7
    COL_CAPITAL = 8
    COL_FOREIGN = 9
    COL_STATUS = 10

    def __init__(
        self, repository: Repository, project: Project, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.project = project
        self.worker: AnalysisWorker | None = None
        self._loading = False
        self._row_ids_by_visual_row: list[int] = []
        self.setObjectName("root")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        header = QHBoxLayout()
        back_button = QPushButton("← К проектам")
        back_button.clicked.connect(self.back_requested.emit)
        header.addWidget(back_button)
        project_title = QLabel(project.name)
        project_title.setObjectName("projectTitle")
        header.addWidget(project_title)
        header.addStretch()
        self.codex_label = QLabel(
            "Codex SDK: установлен" if codex_sdk_available() else "Codex SDK: не установлен"
        )
        header.addWidget(self.codex_label)
        outer.addLayout(header)

        explanation = QLabel(
            "Блок № 1 · Добавляйте только нужные локации. Пустые строки не рассчитываются. "
            f"Максимум — {MAX_BLOCK1_ROWS}."
        )
        explanation.setObjectName("subtitle")
        outer.addWidget(explanation)

        splitter = QSplitter(Qt.Horizontal)
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)

        table_actions = QHBoxLayout()
        self.add_button = QPushButton("+ Добавить строку")
        self.add_button.clicked.connect(self._add_row)
        table_actions.addWidget(self.add_button)
        self.delete_button = QPushButton("Удалить выбранную")
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(self._delete_selected)
        table_actions.addWidget(self.delete_button)
        table_actions.addStretch()
        self.row_counter = QLabel()
        table_actions.addWidget(self.row_counter)
        table_layout.addLayout(table_actions)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            [
                "№",
                "Страна*",
                "Регион*",
                "Геолокация",
                "Цель проекта*",
                "Актуальность норм",
                "Земля",
                "Форма компании",
                "Капитал",
                "Иностранные учредители",
                "Статус",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setColumnWidth(self.COL_NUMBER, 42)
        self.table.setColumnWidth(self.COL_COUNTRY, 150)
        self.table.setColumnWidth(self.COL_REGION, 150)
        self.table.setColumnWidth(self.COL_MAP, 120)
        self.table.setColumnWidth(self.COL_GOAL, 280)
        self.table.setColumnWidth(self.COL_FRESHNESS, 190)
        for column in (self.COL_LAND, self.COL_ENTITY, self.COL_CAPITAL, self.COL_FOREIGN):
            self.table.setColumnWidth(column, 210)
        self.table.setColumnWidth(self.COL_STATUS, 150)
        self.table.currentCellChanged.connect(self._show_current_details)
        table_layout.addWidget(self.table)

        calculation_bar = QHBoxLayout()
        calculation_bar.addWidget(QLabel("Режим:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(
            "Стандартный расчёт · ориентир 3–7 минут", "standard"
        )
        self.mode_combo.addItem(
            "Глубокая проверка · ориентир 7–12 минут", "deep"
        )
        self.mode_combo.addItem("Обновить источники · без кэша", "refresh")
        self.mode_combo.setMinimumWidth(310)
        calculation_bar.addWidget(self.mode_combo)
        self.calculate_button = QPushButton("Расчет блока №1")
        self.calculate_button.setObjectName("primary")
        self.calculate_button.clicked.connect(lambda: self._start_calculation())
        calculation_bar.addWidget(self.calculate_button)
        self.stop_button = QPushButton("Остановить")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_calculation)
        calculation_bar.addWidget(self.stop_button)
        self.progress_label = QLabel("Данные сохраняются автоматически")
        calculation_bar.addWidget(self.progress_label)
        calculation_bar.addStretch()
        self.next_button = QPushButton("Блок № 2 →")
        self.next_button.clicked.connect(self.next_requested.emit)
        calculation_bar.addWidget(self.next_button)
        table_layout.addLayout(calculation_bar)

        splitter.addWidget(table_container)

        details_container = QWidget()
        details_layout = QVBoxLayout(details_container)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_title = QLabel("Подробное заключение")
        details_title.setObjectName("projectTitle")
        details_layout.addWidget(details_title)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.details.setHtml(
            "<p>Выберите рассчитанную строку, чтобы увидеть выводы и источники.</p>"
        )
        details_layout.addWidget(self.details)
        splitter.addWidget(details_container)
        splitter.setSizes([1100, 430])
        outer.addWidget(splitter, 1)

        self.reload_rows()

    def reload_rows(self, selected_row_id: int | None = None) -> None:
        self._loading = True
        rows = self.repository.list_block1_rows(self.project.id)
        self._row_ids_by_visual_row = [row.id for row in rows]
        self.table.setRowCount(len(rows))
        for visual_row, row in enumerate(rows):
            self._populate_row(visual_row, row)
            self.table.setRowHeight(visual_row, 68)
        self._loading = False
        self.row_counter.setText(f"Строк: {len(rows)} из {MAX_BLOCK1_ROWS}")
        self.add_button.setEnabled(len(rows) < MAX_BLOCK1_ROWS and self.worker is None)
        self.delete_button.setEnabled(bool(rows) and self.worker is None)

        if selected_row_id in self._row_ids_by_visual_row:
            row_index = self._row_ids_by_visual_row.index(selected_row_id)
            self.table.selectRow(row_index)
            self._show_details_for_row(selected_row_id)
        elif rows:
            self.table.selectRow(0)
            self._show_details_for_row(rows[0].id)
        else:
            self.details.setHtml(
                "<h3>В проекте пока нет локаций</h3>"
                "<p>Нажмите «+ Добавить строку», затем выберите страну, укажите регион и цель.</p>"
            )

    def _populate_row(self, visual_row: int, row: Block1Row) -> None:
        number = QTableWidgetItem(str(visual_row + 1))
        number.setFlags(number.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(visual_row, self.COL_NUMBER, number)

        country = QComboBox()
        country.setEditable(True)
        country.addItem("")
        country.addItems(AFRICAN_COUNTRIES_RU)
        country.setCurrentText(row.country)
        country.activated.connect(
            lambda _index, row_id=row.id: self._widget_input_changed(row_id)
        )
        if country.lineEdit() is not None:
            country.lineEdit().editingFinished.connect(
                lambda row_id=row.id: self._widget_input_changed(row_id)
            )
        self.table.setCellWidget(visual_row, self.COL_COUNTRY, country)

        region = QLineEdit(row.region)
        region.setPlaceholderText("Регион или город")
        region.editingFinished.connect(
            lambda row_id=row.id: self._widget_input_changed(row_id)
        )
        self.table.setCellWidget(visual_row, self.COL_REGION, region)

        map_button = QPushButton("Открыть карту" if row.map_url else "Будет создана")
        map_button.setEnabled(bool(row.map_url))
        map_button.setProperty("map_url", row.map_url)
        map_button.clicked.connect(
            lambda _checked=False, button=map_button: self._open_map(button)
        )
        self.table.setCellWidget(visual_row, self.COL_MAP, map_button)

        goal = QComboBox()
        goal.addItem("Выберите цель", "")
        for code, label in PROJECT_GOALS:
            goal.addItem(label, code)
        selected_index = goal.findData(row.goal_code)
        goal.setCurrentIndex(max(0, selected_index))
        goal.currentIndexChanged.connect(
            lambda _index, row_id=row.id: self._widget_input_changed(row_id)
        )
        self.table.setCellWidget(visual_row, self.COL_GOAL, goal)

        freshness_widget = QWidget()
        freshness_layout = QVBoxLayout(freshness_widget)
        freshness_layout.setContentsMargins(4, 2, 4, 2)
        freshness_layout.setSpacing(2)
        checked_at = self.repository.cache_checked_at(row.country, row.region)
        freshness_label = QLabel(
            f"Актуально на дату: {checked_at[:10]}" if checked_at else "Нормы ещё не найдены"
        )
        freshness_label.setWordWrap(True)
        freshness_layout.addWidget(freshness_label)
        refresh_button = QPushButton("Актуализировать?")
        refresh_button.setEnabled(not bool(row.missing_fields()) and self.worker is None)
        refresh_button.clicked.connect(
            lambda _checked=False, row_id=row.id: self._actualize_row(row_id)
        )
        freshness_layout.addWidget(refresh_button)
        self.table.setCellWidget(visual_row, self.COL_FRESHNESS, freshness_widget)

        self._update_output_cells(visual_row, row)

    @staticmethod
    def _open_map(button: QPushButton) -> None:
        url = str(button.property("map_url") or "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _update_output_cells(self, visual_row: int, row: Block1Row) -> None:
        map_button = self.table.cellWidget(visual_row, self.COL_MAP)
        if isinstance(map_button, QPushButton):
            map_button.setProperty("map_url", row.map_url)
            map_button.setText("Открыть карту" if row.map_url else "Будет создана")
            map_button.setEnabled(bool(row.map_url))

        analysis = self.repository.get_current_analysis(row.id)
        summaries = (
            analysis.land_rights.summary if analysis else "—",
            analysis.recommended_entity.summary if analysis else "—",
            analysis.capital_requirements.summary if analysis else "—",
            analysis.foreign_company_rules.summary if analysis else "—",
        )
        for column, summary in zip(
            (self.COL_LAND, self.COL_ENTITY, self.COL_CAPITAL, self.COL_FOREIGN),
            summaries,
            strict=True,
        ):
            item = self.table.item(visual_row, column) or QTableWidgetItem()
            item.setText(summary)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setToolTip(summary)
            self.table.setItem(visual_row, column, item)

        status_label = STATUS_LABELS.get(row.status, row.status)
        if row.error_message:
            status_label += f"\n{row.error_message}"
        status_item = self.table.item(visual_row, self.COL_STATUS) or QTableWidgetItem()
        status_item.setText(status_label)
        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
        status_item.setBackground(QColor("transparent"))
        if row.status == "ready":
            status_item.setBackground(QColor("#dff3e8"))
        elif row.status == "error":
            status_item.setBackground(QColor("#fde4e4"))
        elif row.status in {"needs_calculation", "partial"}:
            status_item.setBackground(QColor("#fff2cf"))
        self.table.setItem(visual_row, self.COL_STATUS, status_item)

    def _add_row(self) -> None:
        try:
            row = self.repository.create_block1_row(self.project.id)
        except ValueError as exc:
            QMessageBox.information(self, "Лимит строк", str(exc))
            return
        self.reload_rows(row.id)

    def _delete_selected(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        visual_row = selected[0].row()
        row_id = self._row_ids_by_visual_row[visual_row]
        history_count = self.repository.analysis_history_count(row_id)
        if history_count:
            answer = QMessageBox.question(
                self,
                "Удалить строку?",
                "У строки есть результаты анализа. Удалить её из проекта вместе с историей?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.repository.delete_block1_row(row_id)
        self.reload_rows()

    def _read_widget_values(self, row_id: int) -> tuple[str, str, str]:
        visual_row = self._row_ids_by_visual_row.index(row_id)
        country_widget = self.table.cellWidget(visual_row, self.COL_COUNTRY)
        region_widget = self.table.cellWidget(visual_row, self.COL_REGION)
        goal_widget = self.table.cellWidget(visual_row, self.COL_GOAL)
        assert isinstance(country_widget, QComboBox)
        assert isinstance(region_widget, QLineEdit)
        assert isinstance(goal_widget, QComboBox)
        return (
            country_widget.currentText(),
            region_widget.text(),
            str(goal_widget.currentData() or ""),
        )

    def _widget_input_changed(self, row_id: int) -> None:
        if self._loading or self.worker is not None:
            return
        country, region, goal_code = self._read_widget_values(row_id)
        updated = self.repository.update_block1_input(
            row_id,
            country=country,
            region=region,
            goal_code=goal_code,
        )
        visual_row = self._row_ids_by_visual_row.index(updated.id)
        self._update_output_cells(visual_row, updated)
        self._show_details_for_row(updated.id)

    def _sync_all_inputs(self) -> list[Block1Row]:
        for row_id in list(self._row_ids_by_visual_row):
            country, region, goal_code = self._read_widget_values(row_id)
            self.repository.update_block1_input(
                row_id,
                country=country,
                region=region,
                goal_code=goal_code,
            )
        return self.repository.list_block1_rows(self.project.id)

    def _actualize_row(self, row_id: int) -> None:
        self._start_calculation({row_id})

    def _start_calculation(self, force_row_ids: set[int] | None = None) -> None:
        rows = self._sync_all_inputs()
        force_row_ids = set(force_row_ids or set())
        mode = str(self.mode_combo.currentData() or "standard")
        if mode == "refresh" and not force_row_ids:
            force_row_ids = {row.id for row in rows if not row.missing_fields()}
        candidates: list[Block1Row] = []
        partial_messages: list[str] = []
        for visual_index, row in enumerate(rows, start=1):
            if row.is_empty:
                continue
            missing = row.missing_fields()
            if missing:
                self.repository.set_row_status(
                    row.id, "partial", "Не заполнено: " + ", ".join(missing)
                )
                partial_messages.append(
                    f"Строка {visual_index}: заполните {', '.join(missing)}."
                )
                continue
            if (
                row.id in force_row_ids
                or row.status != "ready"
                or self.repository.get_current_analysis(row.id) is None
            ):
                candidates.append(row)

        self.reload_rows()
        if not candidates:
            message = (
                "Нет новых или изменённых данных для расчёта."
                if rows
                else "Добавьте хотя бы одну строку."
            )
            if partial_messages:
                message += "\n\n" + "\n".join(partial_messages)
            QMessageBox.information(self, "Расчёт не запущен", message)
            return

        if partial_messages:
            QMessageBox.information(
                self,
                "Некоторые строки пропущены",
                "\n".join(partial_messages)
                + "\n\nОстальные заполненные строки будут рассчитаны.",
            )

        self._set_running(True)
        self.progress_label.setText(f"Подготовлено строк: {len(candidates)}")
        self.worker = AnalysisWorker(
            self.repository, candidates, mode=mode, force_refresh_ids=force_row_ids
        )
        self.worker.row_status.connect(self._on_row_status)
        self.worker.row_finished.connect(self._on_row_finished)
        self.worker.row_failed.connect(self._on_row_failed)
        self.worker.batch_finished.connect(self._on_batch_finished)
        self.worker.start()

    def _stop_calculation(self) -> None:
        if self.worker is not None:
            self.worker.request_stop()
            self.progress_label.setText("Остановка после текущего запроса…")
            self.stop_button.setEnabled(False)

    def _set_running(self, running: bool) -> None:
        self.calculate_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.mode_combo.setEnabled(not running)
        self.next_button.setEnabled(not running)
        self.add_button.setEnabled(not running and len(self._row_ids_by_visual_row) < MAX_BLOCK1_ROWS)
        self.delete_button.setEnabled(not running and bool(self._row_ids_by_visual_row))
        self.table.setEnabled(not running)

    def _on_row_status(self, row_id: int, status: str, message: str) -> None:
        label = STATUS_LABELS.get(status, status)
        self.progress_label.setText(f"{label}: {message}")
        self.reload_rows(row_id)

    def _on_row_finished(self, row_id: int) -> None:
        self.progress_label.setText("Строка рассчитана и сохранена")
        self.reload_rows(row_id)

    def _on_row_failed(self, row_id: int, message: str) -> None:
        self.progress_label.setText("Ошибка расчёта")
        self.reload_rows(row_id)

    def _on_batch_finished(self, success: int, failed: int, stopped: bool) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_running(False)
        self.reload_rows()
        if stopped:
            summary = f"Расчёт остановлен. Готово: {success}, ошибок: {failed}."
        else:
            summary = f"Расчёт завершён. Готово: {success}, ошибок: {failed}."
        self.progress_label.setText(summary)
        if failed:
            QMessageBox.warning(
                self,
                "Расчёт завершён с ошибками",
                summary
                + "\n\nВыберите строку со статусом «Ошибка», чтобы увидеть сообщение.",
            )

    def _show_current_details(self, current_row: int, _column: int, *_args: int) -> None:
        if 0 <= current_row < len(self._row_ids_by_visual_row):
            self._show_details_for_row(self._row_ids_by_visual_row[current_row])

    def _show_details_for_row(self, row_id: int) -> None:
        row = self.repository.get_block1_row(row_id)
        if row is None:
            return
        analysis = self.repository.get_current_analysis(row_id)
        if analysis is None:
            error = f"<p><b>Ошибка:</b> {html.escape(row.error_message)}</p>" if row.error_message else ""
            self.details.setHtml(
                f"<h2>{html.escape(row.country or 'Новая строка')} · "
                f"{html.escape(row.region or 'регион не указан')}</h2>"
                f"<p><b>Статус:</b> {html.escape(STATUS_LABELS.get(row.status, row.status))}</p>"
                f"{error}<p>После заполнения исходных данных нажмите «Расчет блока №1».</p>"
            )
            return
        self.details.setHtml(self._analysis_html(analysis, row))

    @staticmethod
    def _analysis_html(analysis: Block1Analysis, row: Block1Row) -> str:
        def finding_html(title: str, finding: LegalFinding) -> str:
            sources = "".join(
                "<li>"
                f"<a href='{html.escape(str(source.url))}'>{html.escape(source.title)}</a>"
                f" — {html.escape(source.issuer or 'орган не указан')}"
                f"; {'официальный источник' if source.official else 'вторичный источник'}"
                f"<br><small>{html.escape(source.supports)}</small>"
                "</li>"
                for source in finding.sources
            ) or "<li>Источники не указаны</li>"
            legal_basis = "".join(
                f"<li>{html.escape(item)}</li>" for item in finding.legal_basis
            ) or "<li>Не указано</li>"
            caveats = "".join(
                f"<li>{html.escape(item)}</li>" for item in finding.caveats
            ) or "<li>Нет</li>"
            return (
                f"<h3>{html.escape(title)}</h3>"
                f"<p><b>Кратко:</b> {html.escape(finding.summary)}</p>"
                f"<p>{html.escape(finding.conclusion)}</p>"
                f"<p><b>Правовая база</b></p><ul>{legal_basis}</ul>"
                f"<p><b>Оговорки</b></p><ul>{caveats}</ul>"
                f"<p><b>Проверка:</b> {html.escape(finding.verification_status)}; "
                f"<b>уверенность:</b> {html.escape(finding.confidence)}</p>"
                f"<p><b>Источники</b></p><ul>{sources}</ul>"
            )

        contradictions = "".join(
            f"<li>{html.escape(item)}</li>" for item in analysis.contradictions
        ) or "<li>Не выявлены</li>"
        questions = "".join(
            f"<li>{html.escape(item)}</li>" for item in analysis.questions_for_local_counsel
        ) or "<li>Не сформулированы</li>"
        return (
            f"<h2>{html.escape(analysis.country)} · {html.escape(analysis.region)}</h2>"
            f"<p><b>Цель:</b> {html.escape(analysis.goal)}</p>"
            f"<p><b>Геолокация:</b> <a href='{html.escape(row.map_url)}'>Google Maps</a></p>"
            f"<p><b>Проверено:</b> {html.escape(analysis.checked_at)}</p>"
            f"<p>{html.escape(analysis.location_context)}</p>"
            + finding_html("1. Права и ограничения на землю", analysis.land_rights)
            + finding_html("2. Рекомендуемая форма организации", analysis.recommended_entity)
            + finding_html("3. Требования к капиталу", analysis.capital_requirements)
            + finding_html("4. Правила для иностранных учредителей", analysis.foreign_company_rules)
            + f"<h3>Противоречия</h3><ul>{contradictions}</ul>"
            + f"<h3>Вопросы местному юристу</h3><ul>{questions}</ul>"
            + f"<p><i>{html.escape(analysis.disclaimer)}</i></p>"
        )


class MainWindow(QMainWindow):
    def __init__(self, repository: Repository) -> None:
        super().__init__()
        self.repository = repository
        self.setWindowTitle("African Villas — блоки № 1–3")
        self.resize(1600, 920)
        QApplication.instance().setStyleSheet(APP_STYLE)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.start_page = StartPage(repository)
        self.start_page.project_opened.connect(self.open_project)
        self.stack.addWidget(self.start_page)

    def open_project(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        if project is None:
            QMessageBox.warning(self, "Ошибка", "Проект не найден.")
            return
        page = Block1Page(self.repository, project)
        page.back_requested.connect(self.show_start_page)
        page.next_requested.connect(lambda: self.open_block2(project_id))
        self._replace_current(page)

    def open_block2(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        if project is None:
            return
        page = Block2Page(self.repository, project)
        page.back_requested.connect(lambda: self.open_project(project_id))
        page.next_requested.connect(lambda: self.open_block3(project_id))
        self._replace_current(page)

    def open_block3(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        if project is None:
            return
        page = Block3Page(self.repository, project)
        page.back_requested.connect(lambda: self.open_block2(project_id))
        self._replace_current(page)

    def _replace_current(self, page: QWidget) -> None:
        current = self.stack.currentWidget()
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        if current is not self.start_page:
            self.stack.removeWidget(current)
            current.deleteLater()

    def show_start_page(self) -> None:
        current = self.stack.currentWidget()
        if isinstance(current, Block1Page) and current.worker is not None:
            QMessageBox.information(
                self,
                "Выполняется расчёт",
                "Сначала дождитесь завершения расчёта или нажмите «Остановить».",
            )
            return
        self.start_page.refresh()
        self.stack.setCurrentWidget(self.start_page)
        if current is not self.start_page:
            self.stack.removeWidget(current)
            current.deleteLater()

    def closeEvent(self, event: QCloseEvent) -> None:
        current = self.stack.currentWidget()
        if isinstance(current, Block1Page) and current.worker is not None:
            QMessageBox.information(
                self,
                "Выполняется расчёт",
                "Нажмите «Остановить» и дождитесь завершения текущего запроса перед закрытием программы.",
            )
            event.ignore()
            return
        event.accept()
