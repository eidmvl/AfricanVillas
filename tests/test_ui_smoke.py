from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from african_villas.database import Repository  # noqa: E402
from african_villas.block2_ui import Block2Page  # noqa: E402
from african_villas.block3_ui import Block3Page, EstimateWorkspaceDialog  # noqa: E402
from african_villas.ui import Block1Page, MainWindow, StartPage  # noqa: E402


def test_main_window_and_empty_project(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "ui.db")
    project = repository.create_project("UI test")
    window = MainWindow(repository)
    window.open_project(project.id)
    app.processEvents()

    page = window.stack.currentWidget()
    assert isinstance(page, Block1Page)
    assert page.table.rowCount() == 0
    assert page.calculate_button.text() == "Расчет блока №1"

    page._add_row()
    app.processEvents()
    assert page.table.rowCount() == 1
    assert repository.list_block1_rows(project.id)[0].is_empty
    window.close()


def test_navigation_to_block2(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "ui2.db")
    project = repository.create_project("Block 2 UI")
    row = repository.create_block1_row(project.id)
    repository.update_block1_input(
        row.id,
        country="Кения",
        region="Найроби",
        goal_code="VILLAS_FOR_SALE",
    )
    window = MainWindow(repository)
    window.open_project(project.id)
    window.open_block2(project.id)
    app.processEvents()
    page = window.stack.currentWidget()
    assert isinstance(page, Block2Page)
    assert page.table.rowCount() == 1
    window.close()


def test_navigation_and_workspace_for_block3(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "ui3.db")
    project = repository.create_project("Block 3 UI")
    row = repository.create_block1_row(project.id)
    repository.update_block1_input(
        row.id,
        country="Танзания",
        region="Занзибар",
        goal_code="VILLAS_FOR_SALE",
    )
    repository.ensure_block2_scenarios(project.id)
    scenario = repository.list_block2_scenarios(project.id)[0]
    repository.update_block2_scenario(
        scenario.id, initial_land_m2=1000, object_land_m2=500, footprint_m2=100
    )
    repository.update_block2_floor(scenario.id, 1, "91–120 м²", 100)
    window = MainWindow(repository)
    window.open_block3(project.id)
    app.processEvents()
    page = window.stack.currentWidget()
    assert isinstance(page, Block3Page)
    assert page.calculate_button.text() == "Расчет блока №3"
    assert page.scenario_combo.count() == 1
    estimate_id = page.current_estimate_id()
    assert estimate_id is not None
    dialog = EstimateWorkspaceDialog(repository, project, estimate_id, page)
    app.processEvents()
    assert dialog.tabs.count() == 5
    assert dialog.documents_table.rowCount() == 0
    dialog.close()
    window.close()


def test_start_page_opens_first_project_without_manual_selection(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "start.db")
    project = repository.create_project("First")
    page = StartPage(repository)
    opened: list[int] = []
    page.project_opened.connect(opened.append)
    assert page.projects_table.currentRow() == 0
    page.open_button.click()
    app.processEvents()
    assert opened == [project.id]
