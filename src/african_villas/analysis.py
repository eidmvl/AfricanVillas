from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from .models import (
    Block1Analysis,
    EvidenceSource,
    JurisdictionResearch,
    LandSpecialistReport,
    LegalFinding,
    SpecialistReport,
    utc_now_iso,
)

CODEX_MODEL = "gpt-5.6-luna"
CODEX_EFFORT = "high"
SOURCE_POLICY = "official_legislation_only; 2-4_sources_per_section"
StatusCallback = Callable[[str, str], None]
SchemaModel = TypeVar("SchemaModel", bound=BaseModel)


class CodexUnavailableError(RuntimeError):
    pass


def codex_sdk_available() -> bool:
    return importlib.util.find_spec("openai_codex") is not None


async def authenticate_codex_client(codex: Any) -> None:
    """Use explicit API-key auth for unattended runs when a key is configured."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key:
        await codex.login_api_key(api_key)


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Codex не вернул JSON-объект")
    return json.loads(cleaned[start : end + 1])


def codex_output_schema(
    model_type: type[SchemaModel] = Block1Analysis,
) -> dict[str, Any]:
    """Create the strict schema expected by Codex structured output."""
    schema = model_type.model_json_schema()

    def normalize(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["additionalProperties"] = False
                node["required"] = list(properties)
            for value in node.values():
                normalize(value)
        elif isinstance(node, list):
            for value in node:
                normalize(value)

    normalize(schema)
    return schema


SECTION_INSTRUCTIONS: dict[str, str] = {
    "land_rights": (
        "Права иностранного инвестора на землю: собственность, аренда, концессия, "
        "производные права и ограничения. Дополнительно извлеки местные нормы планирования "
        "для машиночитаемого local_rules: минимальный участок, покрытие, FAR, этажность, "
        "высота, отступы, инфраструктура и парковка. Не угадывай отсутствующие числа."
    ),
    "recommended_entity": (
        "Разрешённые организационно-правовые формы и обоснованная рекомендация для "
        "иностранного девелопера. Чётко отделяй норму закона от деловой рекомендации."
    ),
    "capital_requirements": (
        "Раздельно проверь минимальный уставный капитал, порог регистрации иностранной "
        "инвестиции и отраслевые инвестиционные минимумы. Не объединяй эти показатели."
    ),
    "foreign_company_rules": (
        "Правила учреждения местной компании и филиала: иностранное владение, местный "
        "участник, директор, представитель, секретарь, адрес, лицензии и регистрация инвестиций."
    ),
}


def _specialist_prompt(
    section: str, country: str, region: str, goals: Sequence[str]
) -> str:
    goal_text = "; ".join(dict.fromkeys(goals)) or "девелопмент недвижимости"
    return f"""
Ты — один из четырёх независимых юридических специалистов. Исследуй только свой раздел.
Юрисдикция: {country}, регион: {region}. Контекст проектов: {goal_text}.
Раздел: {section}. Задача: {SECTION_INSTRUCTIONS[section]}
Результат сохраняется в общем справочнике по стране и региону: он должен быть применим ко
всем типовым проектам жилого девелопмента, а указанные цели задают приоритет, но не сужают норму.

Правила качества и экономии:
- используй только первичные официальные нормативные источники: закон, кодекс,
  подзаконный акт, официальный вестник или нормативный документ государственного органа;
- коммерческие сайты, блоги, агрегаторы и юридические обзоры не использовать;
- оставь 2–4 лучших источника на раздел; если официальных источников меньше, не заполняй
  пробел предположением и снизь уверенность;
- проверь, действует ли норма и применима ли она к указанному региону;
- official=true ставь только источнику с официальным текстом нормы;
- пиши кратко, не повторяй один вывод в нескольких полях.

Верни только структурированный результат своего раздела. Схема уже передана отдельно.
""".strip()


def _verification_prompt(section: str, deep: bool) -> str:
    scope = (
        "Проведи расширенный повторный фактчекинг каждого существенного вывода и всех ссылок."
        if deep
        else "Точечно перепроверь слабые места, применимость нормы и официальность ссылок."
    )
    return f"""
{scope} Раздел: {section}. Используй контекст и свой предыдущий ответ в этой ветке —
черновик повторно не передаётся. Исправь результат, удали неподтверждённые утверждения,
оставь не более четырёх лучших официальных нормативных источников. Верни полный исправленный
результат раздела по уже переданной схеме, без Markdown.
""".strip()


def _only_official(sources: Sequence[EvidenceSource]) -> list[EvidenceSource]:
    return [source for source in sources if source.official][:4]


def _sanitize_finding(finding: LegalFinding) -> LegalFinding:
    sources = _only_official(finding.sources)
    updates: dict[str, Any] = {"sources": sources}
    if not sources:
        updates.update(confidence="low", verification_status="no_official_source")
        caveats = list(finding.caveats)
        warning = "Вывод не подтверждён официальным нормативным источником."
        if warning not in caveats:
            caveats.append(warning)
        updates["caveats"] = caveats
    return finding.model_copy(update=updates)


def _sanitize_report(report: SpecialistReport) -> SpecialistReport:
    updates: dict[str, Any] = {"finding": _sanitize_finding(report.finding)}
    if isinstance(report, LandSpecialistReport):
        rules = report.local_rules
        rule_sources = _only_official(rules.sources)
        rule_updates: dict[str, Any] = {"sources": rule_sources}
        if not rule_sources:
            rule_updates.update(confidence="low", verification_status="no_official_source")
        updates["local_rules"] = rules.model_copy(update=rule_updates)
    return report.model_copy(update=updates)


def _needs_selective_verification(report: SpecialistReport) -> bool:
    finding = report.finding
    return bool(
        len(_only_official(finding.sources)) < 2
        or finding.verification_status in {"needs_clarification", "no_official_source", "conflict"}
        or report.contradictions
    )


def _deduplicate(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))


class AsyncCodexAnalyzer:
    """Four direct AsyncCodex threads; no coordinator prompt and no nested delegation."""

    def __init__(self) -> None:
        if not codex_sdk_available():
            raise CodexUnavailableError(
                "Codex SDK не установлен. Установите зависимости проекта в .venv."
            )
        from openai_codex import AsyncCodex  # type: ignore[import-not-found]

        self._codex_class = AsyncCodex
        self._codex: Any = None
        self._parallel_slots: asyncio.Semaphore | None = None

    async def __aenter__(self) -> "AsyncCodexAnalyzer":
        self._codex = self._codex_class()
        self._parallel_slots = asyncio.Semaphore(4)
        await self._codex.__aenter__()
        await authenticate_codex_client(self._codex)
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._codex is not None:
            await self._codex.__aexit__(exc_type, exc, traceback)
            self._codex = None

    async def _run_specialist(
        self,
        section: str,
        country: str,
        region: str,
        goals: Sequence[str],
        mode: str,
        status: StatusCallback,
    ) -> SpecialistReport:
        if self._codex is None:
            raise RuntimeError("AsyncCodexAnalyzer должен быть открыт через async with")

        from openai_codex import Sandbox  # type: ignore[import-not-found]
        from openai_codex.types import ReasoningEffort  # type: ignore[import-not-found]

        model_type: type[SpecialistReport]
        model_type = LandSpecialistReport if section == "land_rights" else SpecialistReport
        thread = await self._codex.thread_start(model=CODEX_MODEL, sandbox=Sandbox.read_only)
        status("researching", f"Специалист «{section}» исследует нормативные источники")
        if self._parallel_slots is None:
            raise RuntimeError("Не инициализирован пул параллельных специалистов")
        async with self._parallel_slots:
            response = await thread.run(
                _specialist_prompt(section, country, region, goals),
                effort=ReasoningEffort.high,
                output_schema=codex_output_schema(model_type),
            )
        report = model_type.model_validate(extract_json_object(str(response.final_response)))
        if report.section != section:
            report = report.model_copy(update={"section": section})

        deep = mode == "deep"
        if deep or _needs_selective_verification(report):
            status("verifying", f"Специалист «{section}» перепроверяет свой раздел")
            async with self._parallel_slots:
                verified = await thread.run(
                    _verification_prompt(section, deep),
                    effort=ReasoningEffort.high,
                    output_schema=codex_output_schema(model_type),
                )
            report = model_type.model_validate(
                extract_json_object(str(verified.final_response))
            )
            if report.section != section:
                report = report.model_copy(update={"section": section})
        return _sanitize_report(report)

    async def analyze_jurisdiction(
        self,
        country: str,
        region: str,
        goals: Sequence[str],
        mode: str,
        status: StatusCallback,
    ) -> JurisdictionResearch:
        sections = tuple(SECTION_INSTRUCTIONS)
        reports = await asyncio.gather(
            *(
                self._run_specialist(section, country, region, goals, mode, status)
                for section in sections
            )
        )
        by_section = {report.section: report for report in reports}
        land = by_section["land_rights"]
        if not isinstance(land, LandSpecialistReport):
            raise ValueError("Специалист по земле не вернул справочник местных норм")

        notes = _deduplicate(
            [note for report in reports for note in report.jurisdiction_notes]
        )
        contradictions = _deduplicate(
            [item for report in reports for item in report.contradictions]
        )
        questions = _deduplicate(
            [item for report in reports for item in report.questions_for_local_counsel]
        )
        return JurisdictionResearch(
            country=country,
            region=region,
            checked_at=utc_now_iso(),
            model=CODEX_MODEL,
            reasoning_effort=CODEX_EFFORT,
            source_policy=SOURCE_POLICY,
            location_context=" ".join(notes),
            land_rights=land.finding,
            recommended_entity=by_section["recommended_entity"].finding,
            capital_requirements=by_section["capital_requirements"].finding,
            foreign_company_rules=by_section["foreign_company_rules"].finding,
            local_rules=land.local_rules,
            contradictions=contradictions,
            questions_for_local_counsel=questions,
        )


def assemble_block1_analysis(
    research: JurisdictionResearch, goal_label: str
) -> Block1Analysis:
    """Create a project snapshot without asking Codex to repeat cached research."""
    return Block1Analysis(
        country=research.country,
        region=research.region,
        goal=goal_label,
        checked_at=research.checked_at,
        location_context=research.location_context,
        land_rights=research.land_rights,
        recommended_entity=research.recommended_entity,
        capital_requirements=research.capital_requirements,
        foreign_company_rules=research.foreign_company_rules,
        contradictions=research.contradictions,
        questions_for_local_counsel=research.questions_for_local_counsel,
    )
