from pathlib import Path

import pytest

from african_villas.constants import MAX_BLOCK1_ROWS
from african_villas.database import Repository
from african_villas.models import (
    Block1Analysis,
    EvidenceSource,
    LegalFinding,
)


def sample_finding() -> LegalFinding:
    return LegalFinding(
        summary="Краткий вывод",
        conclusion="Подробный вывод",
        confidence="high",
        verification_status="confirmed",
        sources=[
            EvidenceSource(
                title="Официальный закон",
                issuer="Парламент",
                url="https://example.gov/law",
                official=True,
                supports="Подтверждает вывод",
            )
        ],
    )


def sample_analysis() -> Block1Analysis:
    finding = sample_finding()
    return Block1Analysis(
        country="Танзания",
        region="Занзибар",
        goal="Виллы для продажи",
        checked_at="2026-08-16",
        land_rights=finding,
        recommended_entity=finding,
        capital_requirements=finding,
        foreign_company_rules=finding,
    )


def test_project_rows_and_result_history(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.db")
    project = repository.create_project("Test")
    row = repository.create_block1_row(project.id)
    row = repository.update_block1_input(
        row.id,
        country="Танзания",
        region="Занзибар",
        goal_code="VILLAS_FOR_SALE",
    )
    assert "google.com/maps/search" in row.map_url
    assert row.status == "needs_calculation"

    repository.save_analysis(row.id, sample_analysis())
    assert repository.get_block1_row(row.id).status == "ready"  # type: ignore[union-attr]
    assert repository.get_current_analysis(row.id) is not None

    changed = repository.update_block1_input(
        row.id,
        country="Танзания",
        region="Дар-эс-Салам",
        goal_code="VILLAS_FOR_SALE",
    )
    assert changed.status == "needs_calculation"
    assert repository.get_current_analysis(row.id) is None
    assert repository.analysis_history_count(row.id) == 1


def test_maximum_twenty_rows(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.db")
    project = repository.create_project("Limit")
    for _ in range(MAX_BLOCK1_ROWS):
        repository.create_block1_row(project.id)
    with pytest.raises(ValueError):
        repository.create_block1_row(project.id)

