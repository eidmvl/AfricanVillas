from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


PDF_KEYWORDS = (
    "material", "materials", "specification", "schedule", "legend", "quantity",
    "floor plan", "section", "elevation", "foundation", "concrete", "steel",
    "plumbing", "electrical", "drainage", "roof", "finish", "door", "window",
    "материал", "спецификация", "ведомость", "план", "разрез", "фасад",
    "фундамент", "бетон", "арматур", "электр", "водоснаб", "канализац", "кровл",
)


@dataclass(slots=True)
class PdfInspection:
    path: Path
    sha256: str
    size_bytes: int
    page_count: int
    page_texts: list[str]
    extracted_text: str


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf(path: str | Path, max_text_chars: int = 180_000) -> PdfInspection:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".pdf":
        raise ValueError("Выберите существующий PDF-файл")
    reader = PdfReader(str(source), strict=False)
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                raise ValueError("PDF защищён паролем. Сохраните незашифрованную копию.")
        except Exception as exc:  # noqa: BLE001 - pypdf uses several encryption exceptions
            raise ValueError("PDF защищён паролем. Сохраните незашифрованную копию.") from exc
    page_texts: list[str] = []
    remaining = max_text_chars
    for page in reader.pages:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 - one malformed page must not stop the document
            text = ""
        page_texts.append(text[: min(len(text), 30_000)])
        remaining -= min(len(text), 30_000)
        if remaining <= 0:
            remaining = 0
    joined = "\n\n".join(
        f"--- Страница {index + 1} ---\n{text}"
        for index, text in enumerate(page_texts)
        if text
    )[:max_text_chars]
    return PdfInspection(
        path=source,
        sha256=file_sha256(source),
        size_bytes=source.stat().st_size,
        page_count=len(reader.pages),
        page_texts=page_texts,
        extracted_text=joined,
    )


def select_relevant_pages(page_texts: list[str], limit: int) -> list[int]:
    if not page_texts or limit <= 0:
        return []
    scored: list[tuple[float, int]] = []
    for index, text in enumerate(page_texts):
        folded = text.casefold()
        keyword_hits = sum(folded.count(keyword) for keyword in PDF_KEYWORDS)
        score = keyword_hits * 100 + min(len(text), 20_000) / 1000
        if index == 0:
            score += 10_000
        if index == len(page_texts) - 1:
            score += 500
        scored.append((score, index))
    selected = sorted(index for _score, index in sorted(scored, reverse=True)[:limit])
    return selected


def render_pages(
    pdf_path: str | Path,
    page_indexes: list[int],
    output_dir: str | Path,
    *,
    scale: float = 1.6,
) -> list[str]:
    import pypdfium2 as pdfium

    source = Path(pdf_path).expanduser().resolve()
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(source))
    results: list[str] = []
    try:
        for index in page_indexes:
            if index < 0 or index >= len(document):
                continue
            target = target_dir / f"page-{index + 1}.png"
            if not target.exists() or target.stat().st_size == 0:
                page = document[index]
                bitmap = page.render(scale=scale)
                bitmap.to_pil().save(target, format="PNG", optimize=True)
                bitmap.close()
                page.close()
            results.append(str(target))
    finally:
        document.close()
    return results


def render_analysis_pages(
    inspection: PdfInspection,
    cache_root: str | Path,
    *,
    deep: bool = False,
) -> tuple[list[int], list[str]]:
    limit = 20 if deep else 8
    indexes = select_relevant_pages(inspection.page_texts, limit)
    output_dir = Path(cache_root) / inspection.sha256 / "deep" if deep else Path(cache_root) / inspection.sha256 / "standard"
    return indexes, render_pages(inspection.path, indexes, output_dir)
