from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from reportlab.pdfgen import canvas

from african_villas.block3 import calculate_estimate, quote_is_stale
from african_villas.block3_report import export_estimate_pdf
from african_villas.database import Repository
from african_villas.pdf_pipeline import inspect_pdf, render_analysis_pages


def make_estimate(repository: Repository):
    project = repository.create_project("Villa estimate")
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
        scenario.id,
        initial_land_m2=1_000,
        object_land_m2=500,
        footprint_m2=100,
    )
    repository.update_block2_floor(scenario.id, 1, "91–120 м²", 100)
    estimate = repository.get_block3_estimate_for_scenario(scenario.id)
    return project, repository.get_block2_scenario(scenario.id), estimate


def test_block3_math_storage_and_revision(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "test.db")
    _project, scenario, estimate = make_estimate(repository)
    material = repository.create_material(
        estimate.id,
        description="Concrete",
        quantity=100,
        unit="м³",
        waste_pct=10,
        multiplier=1,
    )
    quote = repository.create_price(
        estimate.id,
        material.id,
        supplier="Supplier",
        unit_price=50,
        price_quantity=20,
        observed_at=date.today().isoformat(),
        valid_until=(date.today() + timedelta(days=10)).isoformat(),
        is_selected=1,
    )
    repository.create_labor(
        estimate.id,
        profession="Mason",
        quantity=100,
        norm_hours=0.5,
        productivity_factor=1.2,
        hourly_rate=10,
    )
    repository.create_resource(
        estimate.id,
        description="Mixer",
        calculation_method="quantity",
        quantity=2,
        unit_rate=100,
    )
    repository.update_block3_estimate(estimate.id, contingency_pct=10)
    estimate = repository.get_block3_estimate(estimate.id)
    dev = repository.list_development_costs(estimate.id)[0]
    repository.update_development_cost(dev.id, amount=100)

    summary = calculate_estimate(
        estimate,
        scenario,
        building_count=2,
        gross_floor_area_m2=200,
        materials=repository.list_materials(estimate.id),
        quotes=repository.list_prices(estimate.id),
        labor=repository.list_labor(estimate.id),
        resources=repository.list_resources(estimate.id),
        development_costs=repository.list_development_costs(estimate.id),
        document_count=1,
    )
    assert summary.materials_total == 300
    assert summary.labor_hours == 60
    assert summary.labor_total == 600
    assert summary.resources_total == 200
    assert summary.construction_total == 1464.1
    assert summary.full_product_total == 1564.1
    assert not quote_is_stale(quote)

    revision = repository.save_estimate_revision(estimate.id, summary.to_payload())
    assert revision.version == 1
    assert repository.get_block3_estimate(estimate.id).status == "accepted"


def test_pdf_ingestion_duplicate_detection_render_and_report(tmp_path: Path) -> None:
    source = tmp_path / "project-sheet.pdf"
    page = canvas.Canvas(str(source))
    page.drawString(72, 780, "A-101 FLOOR PLAN - SCALE 1:100")
    page.drawString(72, 760, "CONCRETE 25 m3")
    page.showPage()
    page.save()

    inspection = inspect_pdf(source)
    assert inspection.page_count == 1
    assert "CONCRETE" in inspection.extracted_text
    indexes, images = render_analysis_pages(inspection, tmp_path / "cache", deep=False)
    assert indexes == [0]
    rendered = Path(images[0])
    assert rendered.is_file() and rendered.stat().st_size > 0

    repository = Repository(tmp_path / "data" / "test.db")
    project, scenario, estimate = make_estimate(repository)
    first = repository.add_block3_document(
        estimate.id,
        source,
        sha256=inspection.sha256,
        size_bytes=inspection.size_bytes,
        page_count=inspection.page_count,
        extracted_text=inspection.extracted_text,
    )
    second = repository.add_block3_document(
        estimate.id,
        source,
        sha256=inspection.sha256,
        size_bytes=inspection.size_bytes,
        page_count=inspection.page_count,
        extracted_text=inspection.extracted_text,
    )
    assert first.id == second.id
    assert len(repository.list_block3_documents(estimate.id)) == 1

    summary = calculate_estimate(
        estimate, scenario, 2, 200, [], [], [], [],
        repository.list_development_costs(estimate.id), document_count=1,
    )
    output = export_estimate_pdf(
        tmp_path / "estimate.pdf",
        project=project,
        scenario_label=scenario.name,
        location_label="Танзания · Занзибар",
        estimate=estimate,
        summary=summary,
        documents=repository.list_block3_documents(estimate.id),
        materials=[],
        quotes=[],
        labor=[],
        resources=[],
        development_costs=repository.list_development_costs(estimate.id),
    )
    report = inspect_pdf(output)
    assert report.page_count >= 2
    assert "Проект и смета" in report.extracted_text
