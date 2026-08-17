from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Block2Scenario, LocalRulesProfile


def area_ranges(start: int, step: int, maximum: int) -> list[tuple[str, float]]:
    ranges: list[tuple[str, float]] = []
    lower = start
    first = True
    while lower <= maximum:
        upper = min(maximum, lower + step if first else lower + step - 1)
        ranges.append((f"{lower}–{upper} м²", (lower + upper) / 2))
        lower = upper + 1
        first = False
    return ranges


LAND_RANGES = area_ranges(150, 50, 6000)
RESIDENTIAL_RANGES = area_ranges(30, 30, 1000)


@dataclass(slots=True)
class Block2Calculation:
    usable_land_m2: float
    site_coverage_pct: float | None
    building_count: int
    saleable_object_count: int
    gross_floor_area_m2: float
    rules_summary: str
    compliance_status: str


def _fmt_number(value: float | int | None) -> str:
    if value is None:
        return "не установлено"
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.1f}".replace(",", " ").replace(".", ",")


def format_percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%".replace(".", ",")


def summarize_rules(rules: LocalRulesProfile | None) -> str:
    if rules is None:
        return "Справочник норм пока не заполнен"
    parts = [
        f"Мин. участок: {_fmt_number(rules.minimum_lot_area_m2)} м²",
        f"Макс. застройка: {format_percent(rules.maximum_site_coverage_pct)}",
        f"Макс. этажей: {_fmt_number(rules.maximum_floors)}",
        f"FAR: {_fmt_number(rules.maximum_floor_area_ratio)}",
    ]
    if rules.zoning_scope:
        parts.append(f"Зона: {rules.zoning_scope}")
    return "; ".join(parts)


def calculate_block2(
    scenario: Block2Scenario,
    goal_code: str,
    floor_areas: list[float],
    rules: LocalRulesProfile | None,
) -> Block2Calculation:
    initial_land = max(0.0, scenario.initial_land_m2)
    object_land = max(0.0, scenario.object_land_m2)
    loss_pct = min(100.0, max(0.0, scenario.infrastructure_pct + scenario.other_losses_pct))
    usable_land = initial_land * (1 - loss_pct / 100)
    building_count = math.floor(usable_land / object_land) if object_land > 0 else 0

    is_land_resale = goal_code == "LAND_INFRASTRUCTURE_RESALE"
    is_apartments = goal_code in {"APARTMENTS_FOR_SALE", "APARTMENTS_FOR_RENT"}
    footprint_total = building_count * max(0.0, scenario.footprint_m2)
    site_coverage = (
        None
        if is_land_resale or initial_land <= 0
        else min(9999.0, footprint_total / initial_land * 100)
    )
    gross_floor_area = building_count * sum(max(0.0, area) for area in floor_areas)

    if is_apartments:
        efficiency = min(100.0, max(0.0, scenario.saleable_efficiency_pct)) / 100
        saleable_area = gross_floor_area * efficiency
        saleable_count = (
            math.floor(saleable_area / scenario.average_unit_m2)
            if scenario.average_unit_m2 > 0
            else 0
        )
    else:
        saleable_count = building_count

    violations: list[str] = []
    known_checks = 0
    if rules is not None:
        if rules.minimum_lot_area_m2 is not None and object_land > 0:
            known_checks += 1
            if object_land < rules.minimum_lot_area_m2:
                violations.append("площадь объекта меньше местного минимума")
        if rules.maximum_site_coverage_pct is not None and site_coverage is not None:
            known_checks += 1
            if site_coverage > rules.maximum_site_coverage_pct:
                violations.append("превышен процент застройки")
        if rules.maximum_floors is not None and not is_land_resale:
            known_checks += 1
            if scenario.floor_count > rules.maximum_floors:
                violations.append("превышена разрешённая этажность")

    if violations:
        compliance = "Не соответствует: " + "; ".join(violations)
    elif known_checks:
        compliance = "Предварительно соответствует найденным нормам"
    else:
        compliance = "Недостаточно нормативных данных для проверки"

    return Block2Calculation(
        usable_land_m2=usable_land,
        site_coverage_pct=site_coverage,
        building_count=building_count,
        saleable_object_count=saleable_count,
        gross_floor_area_m2=gross_floor_area,
        rules_summary=summarize_rules(rules),
        compliance_status=compliance,
    )
