from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import GOAL_LABELS


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(slots=True)
class Project:
    id: int
    uid: str
    name: str
    description: str
    client_name: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class Block1Row:
    id: int
    project_id: int
    position: int
    country: str
    region: str
    goal_code: str
    map_url: str
    user_note: str
    input_hash: str
    status: str
    error_message: str
    created_at: str
    updated_at: str
    calculated_at: str | None

    @property
    def goal_label(self) -> str:
        return GOAL_LABELS.get(self.goal_code, self.goal_code)

    @property
    def is_empty(self) -> bool:
        return not any((self.country.strip(), self.region.strip(), self.goal_code.strip()))

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.country.strip():
            missing.append("страна")
        if not self.region.strip():
            missing.append("регион")
        if not self.goal_code.strip():
            missing.append("цель проекта")
        return missing


@dataclass(slots=True)
class Block2Scenario:
    id: int
    project_id: int
    block1_row_id: int
    name: str
    initial_land_range: str
    initial_land_m2: float
    object_land_range: str
    object_land_m2: float
    footprint_m2: float
    floor_count: int
    infrastructure_proximity_json: str
    infrastructure_pct: float
    other_losses_pct: float
    average_unit_m2: float
    saleable_efficiency_pct: float
    created_at: str
    updated_at: str


@dataclass(slots=True)
class Block3Estimate:
    id: int
    project_id: int
    scenario_id: int
    currency: str
    estimate_stage: str
    parametric_rate_per_m2: float
    schedule_days: int
    hours_per_day: float
    utilization_pct: float
    overhead_pct: float
    profit_pct: float
    contingency_pct: float
    tax_pct: float
    notes: str
    status: str
    accepted_revision_id: int | None
    block2_updated_at: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ProjectDocument:
    id: int
    estimate_id: int
    original_name: str
    stored_path: str
    sha256: str
    size_bytes: int
    page_count: int
    discipline: str
    revision: str
    document_scope: str
    units: str
    scale_status: str
    analysis_status: str
    extracted_text: str
    analysis_json: str
    error_message: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class MaterialItem:
    id: int
    estimate_id: int
    work_package: str
    description: str
    specification: str
    quantity: float
    unit: str
    waste_pct: float
    package_size: float
    multiplier: float
    scope: str
    source_document_id: int | None
    source_page: int | None
    source_note: str
    status: str
    confidence: str
    is_manual: int
    notes: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class PriceQuote:
    id: int
    estimate_id: int
    material_id: int
    supplier: str
    product_name: str
    is_analog: int
    compatibility_status: str
    currency: str
    exchange_rate_to_estimate: float
    fx_observed_at: str
    fx_source_url: str
    unit_price: float
    price_quantity: float
    delivery_cost: float
    duty_cost: float
    tax_cost: float
    url: str
    location: str
    observed_at: str
    valid_until: str
    availability: str
    is_selected: int
    status: str
    notes: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class LaborItem:
    id: int
    estimate_id: int
    work_package: str
    profession: str
    quantity: float
    unit: str
    norm_hours: float
    productivity_factor: float
    planned_days: int
    hourly_rate: float
    source: str
    status: str
    notes: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ResourceItem:
    id: int
    estimate_id: int
    category: str
    description: str
    calculation_method: str
    quantity: float
    unit: str
    unit_rate: float
    duration: float
    includes_materials: int
    includes_labor: int
    includes_equipment: int
    source: str
    status: str
    notes: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class DevelopmentCost:
    id: int
    estimate_id: int
    category_code: str
    label: str
    amount: float
    source: str
    status: str
    notes: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class EstimateRevision:
    id: int
    estimate_id: int
    version: int
    payload_json: str
    created_at: str


def input_fingerprint(country: str, region: str, goal_code: str) -> str:
    normalized = {
        "country": " ".join(country.casefold().split()),
        "region": " ".join(region.casefold().split()),
        "goal_code": goal_code.strip(),
    }
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def jurisdiction_key(country: str, region: str) -> str:
    """Stable cache key shared by all projects and goals in one jurisdiction."""
    normalized = {
        "country": " ".join(country.casefold().split()),
        "region": " ".join(region.casefold().split()),
    }
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSource(StrictModel):
    title: str = Field(min_length=1)
    issuer: str = ""
    url: str = Field(min_length=1)
    document_date: str = ""
    accessed_at: str = ""
    official: bool = False
    supports: str = ""

    @field_validator("url")
    @classmethod
    def validate_web_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Источник должен содержать корректную ссылку http/https")
        return value


Confidence = Literal["high", "medium", "low"]
VerificationStatus = Literal[
    "confirmed",
    "partially_confirmed",
    "needs_clarification",
    "no_official_source",
    "conflict",
]


class LegalFinding(StrictModel):
    summary: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    legal_basis: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: Confidence
    verification_status: VerificationStatus
    sources: list[EvidenceSource] = Field(default_factory=list, max_length=4)


class LocalRulesProfile(StrictModel):
    """Machine-readable planning limits reused by Block 2."""

    jurisdiction_level: str = ""
    zoning_scope: str = ""
    minimum_lot_area_m2: float | None = Field(default=None, ge=0)
    maximum_site_coverage_pct: float | None = Field(default=None, ge=0, le=100)
    maximum_floor_area_ratio: float | None = Field(default=None, ge=0)
    maximum_floors: int | None = Field(default=None, ge=0)
    maximum_height_m: float | None = Field(default=None, ge=0)
    front_setback_m: float | None = Field(default=None, ge=0)
    side_setback_m: float | None = Field(default=None, ge=0)
    rear_setback_m: float | None = Field(default=None, ge=0)
    minimum_infrastructure_pct: float | None = Field(default=None, ge=0, le=100)
    parking_requirement: str = ""
    other_requirements: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: Confidence
    verification_status: VerificationStatus
    sources: list[EvidenceSource] = Field(default_factory=list, max_length=4)


SectionCode = Literal[
    "land_rights",
    "recommended_entity",
    "capital_requirements",
    "foreign_company_rules",
]


class SpecialistReport(StrictModel):
    section: SectionCode
    finding: LegalFinding
    jurisdiction_notes: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    questions_for_local_counsel: list[str] = Field(default_factory=list)


class LandSpecialistReport(SpecialistReport):
    local_rules: LocalRulesProfile


class JurisdictionResearch(StrictModel):
    """Reusable country/region research kept outside a particular project."""

    country: str
    region: str
    checked_at: str
    model: str
    reasoning_effort: str
    source_policy: str
    location_context: str = ""
    land_rights: LegalFinding
    recommended_entity: LegalFinding
    capital_requirements: LegalFinding
    foreign_company_rules: LegalFinding
    local_rules: LocalRulesProfile
    contradictions: list[str] = Field(default_factory=list)
    questions_for_local_counsel: list[str] = Field(default_factory=list)


class Block1Analysis(StrictModel):
    country: str
    region: str
    goal: str
    checked_at: str
    location_context: str = ""
    land_rights: LegalFinding
    recommended_entity: LegalFinding
    capital_requirements: LegalFinding
    foreign_company_rules: LegalFinding
    contradictions: list[str] = Field(default_factory=list)
    questions_for_local_counsel: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Информационный анализ не заменяет заключение лицензированного местного юриста."
    )
