from __future__ import annotations

from pathlib import Path

from african_villas.block2 import calculate_block2
from african_villas.block2_ui import parse_proximity
from african_villas.database import Repository
from african_villas.models import (
    EvidenceSource,
    JurisdictionResearch,
    LegalFinding,
    LocalRulesProfile,
)


def sample_research() -> JurisdictionResearch:
    source = EvidenceSource(
        title="Planning Act",
        issuer="Official Gazette",
        url="https://laws.example.gov/planning",
        official=True,
        supports="Planning limits",
    )
    finding = LegalFinding(
        summary="Summary",
        conclusion="Conclusion",
        confidence="high",
        verification_status="confirmed",
        sources=[source],
    )
    rules = LocalRulesProfile(
        minimum_lot_area_m2=300,
        maximum_site_coverage_pct=40,
        maximum_floors=3,
        confidence="high",
        verification_status="confirmed",
        sources=[source],
    )
    return JurisdictionResearch(
        country="Танзания",
        region="Занзибар",
        checked_at="2026-08-16T10:00:00+03:00",
        model="gpt-5.6-luna",
        reasoning_effort="high",
        source_policy="official_legislation_only",
        land_rights=finding,
        recommended_entity=finding,
        capital_requirements=finding,
        foreign_company_rules=finding,
        local_rules=rules,
    )


def test_jurisdiction_cache_is_shared_and_written_to_folder(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "data" / "test.db")
    repository.save_jurisdiction_research(sample_research())

    cached = repository.get_jurisdiction_research(" танзания ", "ЗАНЗИБАР")
    assert cached is not None
    assert cached.model == "gpt-5.6-luna"
    assert repository.cache_checked_at("Танзания", "Занзибар")
    assert len(list(repository.jurisdiction_cache_dir.glob("*.json"))) == 1


def test_block2_scenario_and_instant_math(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.db")
    project = repository.create_project("Block 2")
    row = repository.create_block1_row(project.id)
    row = repository.update_block1_input(
        row.id,
        country="Танзания",
        region="Занзибар",
        goal_code="VILLAS_FOR_SALE",
    )
    repository.save_jurisdiction_research(sample_research())
    repository.ensure_block2_scenarios(project.id)
    scenario = repository.list_block2_scenarios(project.id)[0]
    repository.update_block2_scenario(
        scenario.id,
        initial_land_m2=10_000,
        object_land_m2=500,
        footprint_m2=150,
        infrastructure_proximity_json=(
            '[{"type":"Асфальтированная дорога","proximity":"До 250 м"}]'
        ),
        infrastructure_pct=20,
        other_losses_pct=5,
    )
    repository.update_block2_floor(scenario.id, 1, "121–150 м²", 150)
    scenario = repository.get_block2_scenario(scenario.id)

    result = calculate_block2(
        scenario,
        row.goal_code,
        [150],
        sample_research().local_rules,
    )
    assert result.usable_land_m2 == 7_500
    assert result.building_count == 15
    assert result.saleable_object_count == 15
    assert result.site_coverage_pct == 22.5
    assert result.compliance_status.startswith("Предварительно соответствует")
    assert parse_proximity(scenario.infrastructure_proximity_json) == [
        {"type": "Асфальтированная дорога", "proximity": "До 250 м"}
    ]


def test_apartment_object_count_rounds_down(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.db")
    project = repository.create_project("Apartments")
    row = repository.create_block1_row(project.id)
    row = repository.update_block1_input(
        row.id,
        country="Кения",
        region="Найроби",
        goal_code="APARTMENTS_FOR_SALE",
    )
    repository.ensure_block2_scenarios(project.id)
    scenario = repository.list_block2_scenarios(project.id)[0]
    repository.update_block2_scenario(
        scenario.id,
        initial_land_m2=2_000,
        object_land_m2=1_000,
        footprint_m2=500,
        average_unit_m2=70,
        saleable_efficiency_pct=80,
    )
    repository.set_block2_floor_count(scenario.id, 2)
    scenario = repository.get_block2_scenario(scenario.id)
    result = calculate_block2(scenario, row.goal_code, [500, 500], None)
    assert result.building_count == 2
    assert result.saleable_object_count == 22
