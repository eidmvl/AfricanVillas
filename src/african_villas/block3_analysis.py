from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analysis import (
    CODEX_MODEL,
    CodexUnavailableError,
    codex_output_schema,
    codex_sdk_available,
    extract_json_object,
)
from .models import MaterialItem, ProjectDocument


StatusCallback = Callable[[str, str], None]


class StrictResult(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaterialSuggestion(StrictResult):
    work_package: str
    description: str
    specification: str
    quantity: float = Field(ge=0)
    unit: str
    waste_pct: float = Field(ge=0, le=100)
    multiplier: float = Field(ge=0)
    scope: str
    source_page: int | None
    source_note: str
    confidence: Literal["high", "medium", "low"]
    requires_confirmation: bool


class LaborSuggestion(StrictResult):
    work_package: str
    profession: str
    quantity: float = Field(ge=0)
    unit: str
    norm_hours: float = Field(ge=0)
    productivity_factor: float = Field(ge=0)
    source: str
    confidence: Literal["high", "medium", "low"]
    requires_confirmation: bool


class DocumentAnalysisResult(StrictResult):
    discipline: str
    revision: str
    document_scope: str
    units: str
    scale_status: str
    summary: str
    missing_documents: list[str]
    warnings: list[str]
    materials: list[MaterialSuggestion]
    labor: list[LaborSuggestion]


class PriceSuggestion(StrictResult):
    material_description: str
    supplier: str
    product_name: str
    is_analog: bool
    compatibility_status: Literal["exact", "equivalent", "conditional", "not_comparable"]
    currency: str
    exchange_rate_to_estimate: float = Field(ge=0)
    fx_observed_at: str
    fx_source_url: str
    unit_price: float = Field(ge=0)
    price_quantity: float = Field(gt=0)
    delivery_cost: float = Field(ge=0)
    duty_cost: float = Field(ge=0)
    tax_cost: float = Field(ge=0)
    url: str
    location: str
    observed_at: str
    valid_until: str
    availability: str
    notes: str
    confidence: Literal["high", "medium", "low"]


class PriceResearchResult(StrictResult):
    quotes: list[PriceSuggestion]
    warnings: list[str]


def _document_prompt(document: ProjectDocument, text: str, pages: Sequence[int]) -> str:
    page_list = ", ".join(str(page + 1) for page in pages)
    return f"""
Ты — специалист по проектной документации и предварительным ведомостям объёмов.
Проанализируй PDF «{document.original_name}». Переданы изображения страниц: {page_list or 'нет'}.
Ниже приложен локально извлечённый текст. Изображения имеют приоритет для чертежей.

Правила:
- не измеряй геометрию по масштабу страницы без напечатанного размера и подтверждения;
- если лист NTS/не в масштабе или масштаб неоднозначен, укажи это;
- не придумывай скрытые конструкции, арматуру, фундамент или инженерные сети;
- количество заполняй только когда оно явно дано, посчитано по читаемым обозначениям или таблице;
- для неопределённой позиции quantity=0, confidence=low, requires_confirmation=true;
- scope объясняет: один объект, типовой этаж, корпус или весь проект;
- source_page — номер страницы PDF, source_note — короткое проверяемое основание;
- norm_hours не выдумывай: если подтверждённой нормы нет, укажи 0 и запроси подтверждение;
- пиши кратко; верни только структурированный JSON по переданной схеме.

Извлечённый текст:
{text[:60000]}
""".strip()


def _price_prompt(materials: Sequence[MaterialItem], country: str, region: str, currency: str) -> str:
    lines = "\n".join(
        f"- {item.description}; спецификация: {item.specification or 'не указана'}; "
        f"единица: {item.unit}; ориентировочное количество: {item.quantity * item.multiplier:g}"
        for item in materials
    )
    return f"""
Ты — специалист по снабжению строительного проекта. Локация доставки: {country}, {region}.
Валюта сметы: {currency}. Найди актуальные предложения поставщиков для позиций ниже.

{lines}

Правила качества:
- используй 2–4 лучших первичных источника поставщиков/производителей на позицию, если они доступны;
- сохраняй фактическую валюту предложения, размер упаковки/ценовую единицу и дату проверки;
- если валюта предложения отличается от {currency}, укажи курс: сколько единиц {currency} приходится на одну единицу валюты предложения, дату и URL официального источника курса; если надежного курса нет, exchange_rate_to_estimate=0;
- если валюта предложения равна {currency}, exchange_rate_to_estimate=1, а поля источника курса могут быть пустыми;
- не называй цену доставленной, если доставка, пошлина или налог неизвестны: оставь их 0 и поясни;
- аналог допускается только после сравнения спецификации; иначе compatibility_status=conditional или not_comparable;
- не выдумывай цену, наличие, срок действия или URL;
- если текущая публичная цена не найдена, не создавай фиктивную котировку, добавь предупреждение;
- верни только структурированный JSON по переданной схеме.
""".strip()


class Block3CodexAnalyzer:
    """Direct AsyncCodex jobs with local page selection and strict cached outputs."""

    def __init__(self) -> None:
        if not codex_sdk_available():
            raise CodexUnavailableError("Codex SDK не установлен")
        from openai_codex import AsyncCodex

        self._codex_class = AsyncCodex
        self._codex = None
        self._slots: asyncio.Semaphore | None = None

    async def __aenter__(self) -> "Block3CodexAnalyzer":
        self._codex = self._codex_class()
        self._slots = asyncio.Semaphore(4)
        await self._codex.__aenter__()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._codex is not None:
            await self._codex.__aexit__(exc_type, exc, traceback)
            self._codex = None

    async def analyze_document(
        self,
        document: ProjectDocument,
        image_paths: Sequence[str],
        page_indexes: Sequence[int],
        *,
        deep: bool,
        status: StatusCallback,
    ) -> DocumentAnalysisResult:
        if self._codex is None or self._slots is None:
            raise RuntimeError("Анализатор должен быть открыт через async with")
        from openai_codex import LocalImageInput, Sandbox, TextInput
        from openai_codex.types import ReasoningEffort

        status("analyzing", f"Codex анализирует {document.original_name}")
        thread = await self._codex.thread_start(model=CODEX_MODEL, sandbox=Sandbox.read_only)
        prompt = _document_prompt(document, document.extracted_text, page_indexes)
        inputs = [TextInput(prompt), *(LocalImageInput(str(Path(path))) for path in image_paths)]
        async with self._slots:
            response = await thread.run(
                inputs,
                effort=ReasoningEffort.high,
                output_schema=codex_output_schema(DocumentAnalysisResult),
            )
        result = DocumentAnalysisResult.model_validate(extract_json_object(str(response.final_response)))
        if deep:
            status("verifying", f"Codex перепроверяет {document.original_name}")
            async with self._slots:
                verified = await thread.run(
                    "Перепроверь каждую извлечённую позицию по уже переданным страницам. "
                    "Удали неподтверждённое, не повышай уверенность без основания. "
                    "Верни полный исправленный JSON по той же схеме.",
                    effort=ReasoningEffort.high,
                    output_schema=codex_output_schema(DocumentAnalysisResult),
                )
            result = DocumentAnalysisResult.model_validate(
                extract_json_object(str(verified.final_response))
            )
        return result

    async def research_prices(
        self,
        materials: Sequence[MaterialItem],
        country: str,
        region: str,
        currency: str,
        status: StatusCallback,
    ) -> list[PriceResearchResult]:
        if self._codex is None or self._slots is None:
            raise RuntimeError("Анализатор должен быть открыт через async with")
        from openai_codex import Sandbox
        from openai_codex.types import ReasoningEffort

        batches = [materials[index:index + 6] for index in range(0, len(materials), 6)]

        async def run_batch(batch: Sequence[MaterialItem]) -> PriceResearchResult:
            status("pricing", f"Поиск цен: {len(batch)} позиций")
            thread = await self._codex.thread_start(model=CODEX_MODEL, sandbox=Sandbox.read_only)
            async with self._slots:
                response = await thread.run(
                    _price_prompt(batch, country, region, currency),
                    effort=ReasoningEffort.high,
                    output_schema=codex_output_schema(PriceResearchResult),
                )
            return PriceResearchResult.model_validate(extract_json_object(str(response.final_response)))

        return list(await asyncio.gather(*(run_batch(batch) for batch in batches)))
