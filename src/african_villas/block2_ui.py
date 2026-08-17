from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .block2 import LAND_RANGES, RESIDENTIAL_RANGES, calculate_block2, format_percent
from .database import Repository
from .models import Block1Row, Block2Scenario, Project


INFRASTRUCTURE_TYPES = (
    "Асфальтированная дорога",
    "Общественный транспорт",
    "Электроснабжение",
    "Центральное водоснабжение",
    "Канализация / очистные сооружения",
    "Газоснабжение",
    "Интернет / оптоволокно",
    "Школа / детский сад",
    "Клиника / больница",
    "Магазин / супермаркет",
    "Пляж / набережная",
    "Центр города / деловой район",
    "Аэропорт",
    "Пожарная часть / экстренные службы",
)

PROXIMITY_RANGES = (
    "На участке / уже подключено",
    "На границе участка",
    "До 250 м",
    "251–500 м",
    "501 м – 1 км",
    "1,1–3 км",
    "3,1–5 км",
    "5,1–10 км",
    "Более 10 км",
    "Расстояние пока неизвестно",
)


def parse_proximity(raw: str) -> list[dict[str, str]]:
    try:
        payload = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [
        {"type": str(item["type"]), "proximity": str(item["proximity"])}
        for item in payload
        if isinstance(item, dict) and item.get("type") and item.get("proximity")
    ]


class InfrastructureDialog(QDialog):
    def __init__(self, raw_value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Близость внешней инфраструктуры")
        self.resize(720, 430)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Выберите вид внешней инфраструктуры и расстояние до неё. Можно добавить "
            "несколько пунктов; они сохраняются для анализа проекта, но не вычитаются из земли."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        add_bar = QHBoxLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItems(INFRASTRUCTURE_TYPES)
        add_bar.addWidget(self.type_combo, 2)
        self.proximity_combo = QComboBox()
        self.proximity_combo.addItems(PROXIMITY_RANGES)
        add_bar.addWidget(self.proximity_combo, 1)
        add_button = QPushButton("+ Добавить")
        add_button.clicked.connect(self._add_or_update)
        add_bar.addWidget(add_button)
        layout.addLayout(add_bar)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Вид инфраструктуры", "Близость"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        for item in parse_proximity(raw_value):
            self._append_row(item["type"], item["proximity"])

        remove_button = QPushButton("Удалить выбранный пункт")
        remove_button.setObjectName("danger")
        remove_button.clicked.connect(self._remove_selected)
        layout.addWidget(remove_button)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _append_row(self, infrastructure_type: str, proximity: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(infrastructure_type))
        self.table.setItem(row, 1, QTableWidgetItem(proximity))

    def _add_or_update(self) -> None:
        infrastructure_type = self.type_combo.currentText()
        proximity = self.proximity_combo.currentText()
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).text() == infrastructure_type:
                self.table.item(row, 1).setText(proximity)
                self.table.selectRow(row)
                return
        self._append_row(infrastructure_type, proximity)
        self.table.selectRow(self.table.rowCount() - 1)

    def _remove_selected(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if selected:
            self.table.removeRow(selected[0].row())

    def entries(self) -> list[dict[str, str]]:
        return [
            {
                "type": self.table.item(row, 0).text(),
                "proximity": self.table.item(row, 1).text(),
            }
            for row in range(self.table.rowCount())
        ]


class RangeAreaWidget(QWidget):
    value_changed = Signal(str, float)

    def __init__(
        self,
        ranges: list[tuple[str, float]],
        selected_range: str,
        exact_value: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._loading = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.combo = QComboBox()
        self.combo.addItem("Выберите диапазон", 0.0)
        for label, midpoint in ranges:
            self.combo.addItem(label, midpoint)
        index = self.combo.findText(selected_range)
        self.combo.setCurrentIndex(max(0, index))
        layout.addWidget(self.combo)

        self.exact = QDoubleSpinBox()
        self.exact.setRange(0, 10_000_000)
        self.exact.setDecimals(1)
        self.exact.setSuffix(" м² точно")
        self.exact.setValue(exact_value)
        layout.addWidget(self.exact)
        self.combo.currentIndexChanged.connect(self._range_selected)
        self.exact.valueChanged.connect(self._emit_value)
        self._loading = False

    def _range_selected(self, _index: int) -> None:
        if self._loading:
            return
        midpoint = float(self.combo.currentData() or 0)
        if midpoint:
            self.exact.setValue(midpoint)
        else:
            self._emit_value()

    def _emit_value(self, *_args: object) -> None:
        if not self._loading:
            self.value_changed.emit(self.combo.currentText(), self.exact.value())


class Block2Page(QWidget):
    back_requested = Signal()
    next_requested = Signal()

    BASE_HEADERS = [
        "№",
        "Сценарий",
        "Локация",
        "Цель",
        "Первоначальная площадь земли",
        "Земля на один объект",
        "Пятно застройки одного объекта, м²",
        "Этажей",
        "Близость внешней инфраструктуры",
        "Земля под внутреннюю инфраструктуру, %",
        "Ограничения и непригодная площадь, %",
        "Средняя площадь единицы, м²",
        "Полезная площадь, %",
    ]
    RESULT_HEADERS = [
        "Доступная земля, м²",
        "Процент застройки участка",
        "Объектов / корпусов",
        "Готовых объектов для реализации",
        "Общая площадь этажей, м²",
        "Местные требования",
        "Проверка ограничений",
    ]

    def __init__(
        self, repository: Repository, project: Project, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.project = project
        self._loading = False
        self._scenario_ids: list[int] = []
        self._max_floors = 0
        self.setObjectName("root")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        header = QHBoxLayout()
        back = QPushButton("← Блок № 1")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        title = QLabel(f"{project.name} · Блок № 2")
        title.setObjectName("projectTitle")
        header.addWidget(title)
        header.addStretch()
        outer.addLayout(header)

        note = QLabel(
            "Математика выполняется локально и мгновенно. Диапазон помогает выбрать типовой "
            "размер, а поле «точно» используется в формулах. Нормы берутся из общего "
            "справочника блока № 1. Внешняя инфраструктура хранится как характеристика "
            "локации; из земли вычитаются только внутренние проезды и ограничения участка."
        )
        note.setObjectName("subtitle")
        note.setWordWrap(True)
        outer.addWidget(note)

        actions = QHBoxLayout()
        add = QPushButton("+ Добавить сценарий")
        add.clicked.connect(self._add_scenario)
        actions.addWidget(add)
        delete = QPushButton("Удалить сценарий")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete_scenario)
        actions.addWidget(delete)
        actions.addStretch()
        self.summary_label = QLabel()
        actions.addWidget(self.summary_label)
        outer.addLayout(actions)

        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        outer.addWidget(self.table, 1)

        navigation = QHBoxLayout()
        navigation.addStretch()
        next_button = QPushButton("Блок № 3 →")
        next_button.setObjectName("primary")
        next_button.setToolTip("Открыть расчет стоимости, материалов и ресурсов")
        next_button.clicked.connect(self.next_requested.emit)
        navigation.addWidget(next_button)
        outer.addLayout(navigation)

        self.repository.ensure_block2_scenarios(project.id)
        self.reload()

    def reload(self, selected_scenario_id: int | None = None) -> None:
        self._loading = True
        scenarios = self.repository.list_block2_scenarios(self.project.id)
        rows_by_id = {
            row.id: row for row in self.repository.list_block1_rows(self.project.id)
        }
        self._scenario_ids = [scenario.id for scenario in scenarios]
        self._max_floors = max((scenario.floor_count for scenario in scenarios), default=0)
        headers = (
            self.BASE_HEADERS
            + [f"Площадь этажа {number}" for number in range(1, self._max_floors + 1)]
            + self.RESULT_HEADERS
        )
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(scenarios))
        self.table.setColumnWidth(0, 42)
        for column in range(1, len(headers)):
            self.table.setColumnWidth(column, 170)
        for visual_row, scenario in enumerate(scenarios):
            block1_row = rows_by_id.get(scenario.block1_row_id)
            if block1_row is not None:
                self._populate_row(visual_row, scenario, block1_row)
            self.table.setRowHeight(visual_row, 90)
        self._loading = False
        self.summary_label.setText(f"Сценариев: {len(scenarios)}")
        if selected_scenario_id in self._scenario_ids:
            self.table.selectRow(self._scenario_ids.index(selected_scenario_id))
        elif scenarios:
            self.table.selectRow(0)

    def _set_readonly(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setToolTip(text)
        self.table.setItem(row, column, item)

    def _spin(
        self,
        value: float,
        maximum: float,
        suffix: str,
        callback: Callable[[float], None],
        enabled: bool = True,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, maximum)
        spin.setDecimals(1)
        spin.setSuffix(suffix)
        spin.setValue(value)
        spin.setEnabled(enabled)
        spin.valueChanged.connect(callback)
        return spin

    def _populate_row(
        self, visual_row: int, scenario: Block2Scenario, block1_row: Block1Row
    ) -> None:
        self._set_readonly(visual_row, 0, str(visual_row + 1))
        name = QLineEdit(scenario.name)
        name.editingFinished.connect(
            lambda sid=scenario.id, edit=name: self._update(sid, name=edit.text())
        )
        self.table.setCellWidget(visual_row, 1, name)
        self._set_readonly(
            visual_row, 2, f"{block1_row.country} · {block1_row.region}"
        )
        self._set_readonly(visual_row, 3, block1_row.goal_label)

        initial = RangeAreaWidget(
            LAND_RANGES, scenario.initial_land_range, scenario.initial_land_m2
        )
        initial.value_changed.connect(
            lambda label, exact, sid=scenario.id: self._update(
                sid, initial_land_range=label, initial_land_m2=exact
            )
        )
        self.table.setCellWidget(visual_row, 4, initial)

        object_land = RangeAreaWidget(
            LAND_RANGES, scenario.object_land_range, scenario.object_land_m2
        )
        object_land.value_changed.connect(
            lambda label, exact, sid=scenario.id: self._update(
                sid, object_land_range=label, object_land_m2=exact
            )
        )
        self.table.setCellWidget(visual_row, 5, object_land)

        is_land = block1_row.goal_code == "LAND_INFRASTRUCTURE_RESALE"
        is_apartments = block1_row.goal_code in {
            "APARTMENTS_FOR_SALE",
            "APARTMENTS_FOR_RENT",
        }
        footprint = self._spin(
            scenario.footprint_m2,
            1_000_000,
            " м²",
            lambda value, sid=scenario.id: self._update(sid, footprint_m2=value),
            not is_land,
        )
        self.table.setCellWidget(visual_row, 6, footprint)

        floors = QSpinBox()
        floors.setRange(0 if is_land else 1, 50)
        floors.setValue(scenario.floor_count)
        floors.setEnabled(not is_land)
        floors.editingFinished.connect(
            lambda sid=scenario.id, spin=floors: self._change_floor_count(sid, spin.value())
        )
        self.table.setCellWidget(visual_row, 7, floors)

        proximity_entries = parse_proximity(scenario.infrastructure_proximity_json)
        proximity_button = QPushButton(
            "Добавить близость" if not proximity_entries else f"Выбрано: {len(proximity_entries)}"
        )
        proximity_summary = "\n".join(
            f"{item['type']}: {item['proximity']}" for item in proximity_entries
        )
        proximity_button.setToolTip(proximity_summary or "Добавьте инфраструктуру рядом")
        proximity_button.clicked.connect(
            lambda _checked=False, sid=scenario.id: self._edit_proximity(sid)
        )
        self.table.setCellWidget(visual_row, 8, proximity_button)

        infrastructure = self._spin(
            scenario.infrastructure_pct,
            100,
            "%",
            lambda value, sid=scenario.id: self._update(sid, infrastructure_pct=value),
        )
        infrastructure.setToolTip(
            "Доля участка под внутренние дороги, проезды, парковки, инженерные сети "
            "и общие территории. Вычитается из полезной площади земли."
        )
        self.table.setCellWidget(visual_row, 9, infrastructure)
        losses = self._spin(
            scenario.other_losses_pct,
            100,
            "%",
            lambda value, sid=scenario.id: self._update(sid, other_losses_pct=value),
        )
        losses.setToolTip(
            "Неиспользуемая земля: обязательные отступы, сервитуты, охранные зоны, "
            "сложный рельеф, водоёмы и неудобная форма участка."
        )
        self.table.setCellWidget(visual_row, 10, losses)
        average = self._spin(
            scenario.average_unit_m2,
            10_000,
            " м²",
            lambda value, sid=scenario.id: self._update(sid, average_unit_m2=value),
            is_apartments,
        )
        self.table.setCellWidget(visual_row, 11, average)
        efficiency = self._spin(
            scenario.saleable_efficiency_pct,
            100,
            "%",
            lambda value, sid=scenario.id: self._update(
                sid, saleable_efficiency_pct=value
            ),
            is_apartments,
        )
        self.table.setCellWidget(visual_row, 12, efficiency)

        floor_values = self.repository.list_block2_floors(scenario.id)
        floor_start = len(self.BASE_HEADERS)
        for floor_number in range(1, self._max_floors + 1):
            if floor_number > scenario.floor_count:
                self._set_readonly(visual_row, floor_start + floor_number - 1, "—")
                continue
            area_range, area_m2 = floor_values.get(floor_number, ("", 0.0))
            floor_widget = RangeAreaWidget(RESIDENTIAL_RANGES, area_range, area_m2)
            floor_widget.value_changed.connect(
                lambda label, exact, sid=scenario.id, number=floor_number: self._update_floor(
                    sid, number, label, exact
                )
            )
            self.table.setCellWidget(
                visual_row, floor_start + floor_number - 1, floor_widget
            )
        self._update_results(visual_row, scenario, block1_row)

    def _update(self, scenario_id: int, **values: object) -> None:
        if self._loading:
            return
        self.repository.update_block2_scenario(scenario_id, **values)
        self._refresh_scenario_results(scenario_id)

    def _update_floor(
        self, scenario_id: int, floor_number: int, label: str, exact: float
    ) -> None:
        if self._loading:
            return
        self.repository.update_block2_floor(scenario_id, floor_number, label, exact)
        self._refresh_scenario_results(scenario_id)

    def _edit_proximity(self, scenario_id: int) -> None:
        scenario = self.repository.get_block2_scenario(scenario_id)
        dialog = InfrastructureDialog(scenario.infrastructure_proximity_json, self)
        if dialog.exec() != QDialog.Accepted:
            return
        payload = json.dumps(dialog.entries(), ensure_ascii=False, separators=(",", ":"))
        self.repository.update_block2_scenario(
            scenario_id, infrastructure_proximity_json=payload
        )
        self.reload(scenario_id)

    def _change_floor_count(self, scenario_id: int, count: int) -> None:
        if self._loading:
            return
        self.repository.set_block2_floor_count(scenario_id, count)
        self.reload(scenario_id)

    def _refresh_scenario_results(self, scenario_id: int) -> None:
        if scenario_id not in self._scenario_ids:
            return
        visual_row = self._scenario_ids.index(scenario_id)
        scenario = self.repository.get_block2_scenario(scenario_id)
        block1_row = self.repository.get_block1_row(scenario.block1_row_id)
        if block1_row is not None:
            self._update_results(visual_row, scenario, block1_row)

    def _update_results(
        self, visual_row: int, scenario: Block2Scenario, block1_row: Block1Row
    ) -> None:
        research = self.repository.get_jurisdiction_research(
            block1_row.country, block1_row.region
        )
        rules = research.local_rules if research else None
        floor_values = self.repository.list_block2_floors(scenario.id)
        calculation = calculate_block2(
            scenario,
            block1_row.goal_code,
            [floor_values.get(number, ("", 0.0))[1] for number in range(1, scenario.floor_count + 1)],
            rules,
        )
        start = len(self.BASE_HEADERS) + self._max_floors
        values = [
            f"{calculation.usable_land_m2:,.1f}".replace(",", " ").replace(".", ","),
            format_percent(calculation.site_coverage_pct),
            str(calculation.building_count),
            str(calculation.saleable_object_count),
            f"{calculation.gross_floor_area_m2:,.1f}".replace(",", " ").replace(".", ","),
            calculation.rules_summary,
            calculation.compliance_status,
        ]
        for offset, value in enumerate(values):
            self._set_readonly(visual_row, start + offset, value)

    def _add_scenario(self) -> None:
        rows = [
            row for row in self.repository.list_block1_rows(self.project.id) if not row.is_empty
        ]
        if not rows:
            QMessageBox.information(
                self, "Нет целей", "Сначала заполните хотя бы одну строку блока № 1."
            )
            return
        labels = [f"{row.country} · {row.region} · {row.goal_label}" for row in rows]
        selected, ok = QInputDialog.getItem(
            self, "Добавить сценарий", "Для какой цели:", labels, 0, False
        )
        if not ok:
            return
        row = rows[labels.index(selected)]
        scenario = self.repository.create_block2_scenario(
            self.project.id, row.id, f"Сценарий {len(self._scenario_ids) + 1}"
        )
        self.reload(scenario.id)

    def _delete_scenario(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        scenario_id = self._scenario_ids[selected[0].row()]
        answer = QMessageBox.question(
            self,
            "Удалить сценарий?",
            "Введённые параметры этого сценария будут удалены.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.repository.delete_block2_scenario(scenario_id)
            self.reload()
