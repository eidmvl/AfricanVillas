from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from .models import (
    Block2Scenario,
    Block3Estimate,
    DevelopmentCost,
    LaborItem,
    MaterialItem,
    PriceQuote,
    ResourceItem,
)


DEVELOPMENT_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("LAND_TRANSACTION", "Земля и оформление"),
    ("DESIGN_PERMITS", "Изыскания, проектирование и разрешения"),
    ("SITE_INFRASTRUCTURE", "Дороги, сети и подготовка участка"),
    ("BUILDINGS_FACILITIES", "Дополнительные объекты и благоустройство вне строительной сметы"),
    ("FURNITURE_EQUIPMENT", "Мебель и оборудование"),
    ("MANAGEMENT_SALES", "Управление, продажи и административные расходы"),
    ("FINANCE_TAX_HOLDING", "Финансирование, налоги и содержание земли"),
    ("RISK_RESERVE", "Резерв и риски"),
)

WORK_PACKAGES: tuple[str, ...] = (
    "Подготовительные работы",
    "Земляные работы",
    "Фундаменты",
    "Каркас и стены",
    "Кровля",
    "Фасады и окна",
    "Внутренняя отделка",
    "Водоснабжение и канализация",
    "Электроснабжение",
    "Вентиляция и кондиционирование",
    "Наружные сети",
    "Дороги и благоустройство",
    "Мебель и оборудование",
    "Прочее",
)

UNITS: tuple[str, ...] = ("шт", "м", "м²", "м³", "кг", "т", "л", "упак.", "компл.", "час", "день")


def _money(value: Decimal | float | int) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _d(value: float | int) -> Decimal:
    return Decimal(str(value))


def _parse_date(value: str) -> date | None:
    if not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def quote_is_stale(quote: PriceQuote, today: date | None = None) -> bool:
    today = today or date.today()
    valid_until = _parse_date(quote.valid_until)
    if valid_until is not None:
        return valid_until < today
    observed = _parse_date(quote.observed_at)
    price_stale = observed is None or observed < today - timedelta(days=30)
    fx_date = _parse_date(quote.fx_observed_at)
    fx_stale = bool(quote.fx_observed_at) and (
        fx_date is None or fx_date < today - timedelta(days=30)
    )
    return price_stale or fx_stale


def material_purchase_quantity(material: MaterialItem, quote: PriceQuote | None) -> float:
    net = max(0.0, material.quantity) * max(0.0, material.multiplier)
    gross = net * (1 + max(0.0, material.waste_pct) / 100)
    basis = max(0.0, quote.price_quantity if quote else material.package_size)
    if basis <= 0:
        return gross
    return math.ceil(gross / basis) * basis


def quote_has_valid_fx(quote: PriceQuote, estimate_currency: str) -> bool:
    if quote.currency.casefold() == estimate_currency.casefold():
        return True
    return (
        quote.exchange_rate_to_estimate > 0
        and _parse_date(quote.fx_observed_at) is not None
        and quote.fx_source_url.startswith(("http://", "https://"))
    )


def material_cost(
    material: MaterialItem,
    quote: PriceQuote | None,
    estimate_currency: str | None = None,
) -> float:
    if quote is None or quote.unit_price < 0:
        return 0.0
    same_currency = not estimate_currency or quote.currency.casefold() == estimate_currency.casefold()
    if estimate_currency and not quote_has_valid_fx(quote, estimate_currency):
        return 0.0
    exchange_rate = 1.0 if same_currency else quote.exchange_rate_to_estimate
    if exchange_rate <= 0:
        return 0.0
    gross = material_purchase_quantity(material, quote)
    basis = max(quote.price_quantity, 1e-12)
    packages = math.ceil(gross / basis)
    total = (
        _d(packages) * _d(quote.unit_price)
        + _d(max(0.0, quote.delivery_cost))
        + _d(max(0.0, quote.duty_cost))
        + _d(max(0.0, quote.tax_cost))
    )
    return _money(total * _d(exchange_rate))


def labor_hours(item: LaborItem) -> float:
    return max(0.0, item.quantity) * max(0.0, item.norm_hours) * max(0.0, item.productivity_factor)


def required_workers(hours: float, days: int, hours_per_day: float, utilization_pct: float) -> int:
    capacity = max(0, days) * max(0.0, hours_per_day) * max(0.0, utilization_pct) / 100
    return math.ceil(hours / capacity) if hours > 0 and capacity > 0 else 0


def resource_cost(item: ResourceItem) -> float:
    quantity = _d(max(0.0, item.quantity))
    rate = _d(max(0.0, item.unit_rate))
    duration = _d(max(0.0, item.duration))
    if item.calculation_method == "fixed":
        return _money(rate)
    if item.calculation_method == "time":
        return _money(duration * rate)
    if item.calculation_method == "quantity_time":
        return _money(quantity * duration * rate)
    return _money(quantity * rate)


@dataclass(slots=True)
class EstimateSummary:
    gross_floor_area_m2: float
    materials_total: float
    labor_hours: float
    labor_total: float
    resources_total: float
    detailed_direct_cost: float
    parametric_cost: float
    construction_base: float
    overhead: float
    profit: float
    contingency: float
    tax: float
    construction_total: float
    development_total: float
    full_product_total: float
    average_workers: int
    peak_workers: int
    unpriced_materials: int
    stale_prices: int
    missing_exchange_rates: int
    open_issues: list[str]
    estimate_level: str
    price_status: str
    category_totals: dict[str, float]

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


def calculate_estimate(
    estimate: Block3Estimate,
    scenario: Block2Scenario,
    building_count: int,
    gross_floor_area_m2: float,
    materials: Iterable[MaterialItem],
    quotes: Iterable[PriceQuote],
    labor: Iterable[LaborItem],
    resources: Iterable[ResourceItem],
    development_costs: Iterable[DevelopmentCost],
    document_count: int = 0,
) -> EstimateSummary:
    materials = list(materials)
    quotes = list(quotes)
    labor = list(labor)
    resources = list(resources)
    development_costs = list(development_costs)

    selected_quotes = {quote.material_id: quote for quote in quotes if quote.is_selected}
    material_total = Decimal("0")
    unpriced = 0
    stale = 0
    missing_fx = 0
    for material in materials:
        quote = selected_quotes.get(material.id)
        if quote is None:
            unpriced += 1
            continue
        if not quote_has_valid_fx(quote, estimate.currency):
            missing_fx += 1
            continue
        material_total += _d(material_cost(material, quote, estimate.currency))
        if quote_is_stale(quote):
            stale += 1

    total_hours = sum(labor_hours(item) for item in labor)
    labor_total = sum(labor_hours(item) * max(0.0, item.hourly_rate) for item in labor)
    resources_total = sum(resource_cost(item) for item in resources)
    detailed_direct = _money(material_total + _d(labor_total) + _d(resources_total))
    parametric = _money(max(0.0, gross_floor_area_m2) * max(0.0, estimate.parametric_rate_per_m2))
    construction_base = detailed_direct if detailed_direct > 0 else parametric

    overhead = _money(_d(construction_base) * _d(max(0.0, estimate.overhead_pct)) / 100)
    profit_base = _d(construction_base) + _d(overhead)
    profit = _money(profit_base * _d(max(0.0, estimate.profit_pct)) / 100)
    contingency_base = profit_base + _d(profit)
    contingency = _money(contingency_base * _d(max(0.0, estimate.contingency_pct)) / 100)
    tax_base = contingency_base + _d(contingency)
    tax = _money(tax_base * _d(max(0.0, estimate.tax_pct)) / 100)
    construction_total = _money(tax_base + _d(tax))

    category_totals = {code: 0.0 for code, _label in DEVELOPMENT_CATEGORIES}
    for item in development_costs:
        category_totals[item.category_code] = _money(
            _d(category_totals.get(item.category_code, 0)) + _d(max(0.0, item.amount))
        )
    development_total = _money(sum(_d(value) for value in category_totals.values()))
    full_total = _money(_d(construction_total) + _d(development_total))

    workers_by_trade = [
        required_workers(
            labor_hours(item),
            item.planned_days or estimate.schedule_days,
            estimate.hours_per_day,
            estimate.utilization_pct,
        )
        for item in labor
    ]
    average_workers = sum(workers_by_trade)
    peak_workers = average_workers

    issues: list[str] = []
    if not materials and construction_base <= 0:
        issues.append("Не добавлены материалы и не задана укрупнённая ставка строительства.")
    if unpriced:
        issues.append(f"Материалов без выбранной цены: {unpriced}.")
    if stale:
        issues.append(f"Устаревших выбранных цен: {stale}.")
    if missing_fx:
        issues.append(f"Цен в другой валюте без подтвержденного курса: {missing_fx}.")
    if not labor:
        issues.append("Не рассчитана трудоёмкость.")
    if document_count == 0:
        issues.append("Проектные PDF не загружены; расчёт является предварительным.")
    if any(item.includes_materials and materials for item in resources):
        issues.append("Есть ресурсы/субподряды, включающие материалы: проверьте двойной счёт.")
    if any(item.includes_labor and labor for item in resources):
        issues.append("Есть ресурсы/субподряды, включающие труд: проверьте двойной счёт.")

    priced_ratio = (len(materials) - unpriced) / len(materials) if materials else 0
    if document_count == 0:
        level = "Предварительная"
    elif materials and priced_ratio >= 0.8 and not stale:
        level = "По текущим ценам"
    elif materials:
        level = "Проектная"
    else:
        level = "Документы загружены"

    if not quotes:
        price_status = "Цены не заполнены"
    elif missing_fx:
        price_status = f"Не хватает валютных курсов: {missing_fx}"
    elif stale:
        price_status = f"Требуют обновления: {stale}"
    elif unpriced:
        price_status = f"Не хватает цен: {unpriced}"
    else:
        newest = max((_parse_date(q.observed_at) for q in quotes if q.is_selected), default=None)
        price_status = f"Актуальны на {newest.strftime('%d.%m.%Y')}" if newest else "Цены проверены"

    return EstimateSummary(
        gross_floor_area_m2=max(0.0, gross_floor_area_m2),
        materials_total=_money(material_total),
        labor_hours=round(total_hours, 2),
        labor_total=_money(labor_total),
        resources_total=_money(resources_total),
        detailed_direct_cost=detailed_direct,
        parametric_cost=parametric,
        construction_base=construction_base,
        overhead=overhead,
        profit=profit,
        contingency=contingency,
        tax=tax,
        construction_total=construction_total,
        development_total=development_total,
        full_product_total=full_total,
        average_workers=average_workers,
        peak_workers=peak_workers,
        unpriced_materials=unpriced,
        stale_prices=stale,
        missing_exchange_rates=missing_fx,
        open_issues=issues,
        estimate_level=level,
        price_status=price_status,
        category_totals=category_totals,
    )
