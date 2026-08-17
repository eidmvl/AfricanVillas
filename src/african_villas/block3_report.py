from __future__ import annotations

from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .block3 import (
    DEVELOPMENT_CATEGORIES,
    EstimateSummary,
    labor_hours,
    material_cost,
    material_purchase_quantity,
    resource_cost,
)
from .models import (
    Block3Estimate,
    DevelopmentCost,
    LaborItem,
    MaterialItem,
    PriceQuote,
    Project,
    ProjectDocument,
    ResourceItem,
)


def _register_fonts() -> tuple[str, str]:
    candidates = [
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf"), "AVArial"),
        (Path("C:/Windows/Fonts/calibri.ttf"), Path("C:/Windows/Fonts/calibrib.ttf"), "AVCalibri"),
    ]
    for regular, bold, family in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont(family, str(regular)))
            pdfmetrics.registerFont(TTFont(f"{family}-Bold", str(bold)))
            return family, f"{family}-Bold"
    return "Helvetica", "Helvetica-Bold"


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}".replace(",", " ")


def export_estimate_pdf(
    output_path: str | Path,
    *,
    project: Project,
    scenario_label: str,
    location_label: str,
    estimate: Block3Estimate,
    summary: EstimateSummary,
    documents: list[ProjectDocument],
    materials: list[MaterialItem],
    quotes: list[PriceQuote],
    labor: list[LaborItem],
    resources: list[ResourceItem],
    development_costs: list[DevelopmentCost],
) -> Path:
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    regular, bold = _register_fonts()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "AVTitle", parent=styles["Title"], fontName=bold, fontSize=20,
        leading=24, textColor=colors.HexColor("#153A31"), alignment=TA_CENTER,
        spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        "AVHeading", parent=styles["Heading2"], fontName=bold, fontSize=13,
        leading=16, textColor=colors.HexColor("#196C57"), spaceBefore=10, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "AVBody", parent=styles["BodyText"], fontName=regular, fontSize=9,
        leading=12, textColor=colors.HexColor("#17202A"), spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        "AVSmall", parent=styles["BodyText"], fontName=regular, fontSize=7.5,
        leading=9, textColor=colors.HexColor("#35434C"),
    ))
    styles.add(ParagraphStyle(
        "AVNumber", parent=styles["AVBody"], alignment=TA_RIGHT,
    ))

    doc = SimpleDocTemplate(
        str(target), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=18 * mm, bottomMargin=17 * mm,
        title=f"Смета — {project.name}", author="African Villas",
    )
    story: list[object] = [
        Paragraph("Блок №3 · Проект и смета", styles["AVTitle"]),
        Paragraph(f"<b>Проект:</b> {escape(project.name)}", styles["AVBody"]),
        Paragraph(f"<b>Сценарий:</b> {escape(scenario_label)}", styles["AVBody"]),
        Paragraph(f"<b>Локация:</b> {escape(location_label)}", styles["AVBody"]),
        Paragraph(
            f"<b>Уровень:</b> {escape(summary.estimate_level)} · "
            f"<b>Статус цен:</b> {escape(summary.price_status)}",
            styles["AVBody"],
        ),
        Spacer(1, 4 * mm),
    ]

    summary_rows = [
        ["Показатель", "Значение"],
        ["Строительная смета", _money(summary.construction_total, estimate.currency)],
        ["Прочие затраты девелопера", _money(summary.development_total, estimate.currency)],
        ["Полная стоимость продукта", _money(summary.full_product_total, estimate.currency)],
        ["Материалы", _money(summary.materials_total, estimate.currency)],
        ["Труд", f"{summary.labor_hours:,.1f} чел.-ч · {_money(summary.labor_total, estimate.currency)}".replace(",", " ")],
        ["Техника и прочие ресурсы", _money(summary.resources_total, estimate.currency)],
        ["Пиковый состав", f"{summary.peak_workers} чел."],
    ]
    story.append(_table(summary_rows, [115 * mm, 50 * mm], regular, bold))

    story.append(Paragraph("Категории полной стоимости", styles["AVHeading"]))
    dev_by_code = {item.category_code: item for item in development_costs}
    category_rows = [["Категория", "Сумма"]]
    for code, label in DEVELOPMENT_CATEGORIES:
        item = dev_by_code.get(code)
        category_rows.append([label, _money(item.amount if item else 0, estimate.currency)])
    category_rows.append(["Строительная часть", _money(summary.construction_total, estimate.currency)])
    story.append(_table(category_rows, [120 * mm, 45 * mm], regular, bold))

    story.append(Paragraph("Исходные документы", styles["AVHeading"]))
    if documents:
        rows = [["Файл", "Дисциплина", "Ревизия", "Статус"]]
        rows.extend([
            [docu.original_name, docu.discipline, docu.revision or "—", docu.analysis_status]
            for docu in documents
        ])
        story.append(_table(rows, [73 * mm, 36 * mm, 22 * mm, 34 * mm], regular, bold))
    else:
        story.append(Paragraph("Проектные PDF не загружены.", styles["AVBody"]))

    story.append(PageBreak())
    story.append(Paragraph("Материалы", styles["AVHeading"]))
    selected = {quote.material_id: quote for quote in quotes if quote.is_selected}
    material_rows = [["Материал", "Кол-во", "Закупка", "Поставщик", "Стоимость"]]
    for item in materials:
        quote = selected.get(item.id)
        purchase = material_purchase_quantity(item, quote)
        cost = material_cost(item, quote, estimate.currency)
        material_rows.append([
            item.description,
            f"{item.quantity * item.multiplier:g} {item.unit}",
            f"{purchase:g} {item.unit}",
            quote.supplier if quote else "Нет цены",
            _money(cost, estimate.currency),
        ])
    story.append(_table(material_rows, [57 * mm, 25 * mm, 25 * mm, 34 * mm, 30 * mm], regular, bold))

    story.append(Paragraph("Труд", styles["AVHeading"]))
    labor_rows = [["Профессия", "Работа", "Чел.-ч", "Ставка", "Стоимость"]]
    for item in labor:
        hours = labor_hours(item)
        labor_rows.append([
            item.profession, item.work_package, f"{hours:,.1f}".replace(",", " "),
            _money(item.hourly_rate, estimate.currency),
            _money(hours * item.hourly_rate, estimate.currency),
        ])
    story.append(_table(labor_rows, [38 * mm, 55 * mm, 22 * mm, 27 * mm, 29 * mm], regular, bold))

    story.append(Paragraph("Прочие ресурсы", styles["AVHeading"]))
    resource_rows = [["Категория", "Ресурс", "Расчёт", "Стоимость"]]
    for item in resources:
        resource_rows.append([
            item.category, item.description,
            f"{item.quantity:g} {item.unit} × {item.duration:g}",
            _money(resource_cost(item), estimate.currency),
        ])
    story.append(_table(resource_rows, [35 * mm, 68 * mm, 32 * mm, 36 * mm], regular, bold))

    story.append(Paragraph("Допущения, пробелы и проверки", styles["AVHeading"]))
    if summary.open_issues:
        for issue in summary.open_issues:
            story.append(Paragraph(f"• {escape(issue)}", styles["AVBody"]))
    else:
        story.append(Paragraph("Критические незакрытые вопросы не выявлены.", styles["AVBody"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "Расчёт является инструментом предварительного и проектного планирования. "
        "Для закупки и договора требуется проверка местным quantity surveyor, инженером и подрядчиком.",
        styles["AVSmall"],
    ))

    def page_footer(canvas, document) -> None:  # noqa: ANN001 - reportlab callback API
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#66737C"))
        canvas.drawString(15 * mm, 9 * mm, "African Villas · Блок №3")
        canvas.drawRightString(195 * mm, 9 * mm, f"Страница {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    return target


def _table(rows: list[list[object]], widths: list[float], regular: str, bold: str) -> Table:
    normalized = [
        [Paragraph(escape(str(value)), ParagraphStyle(
            f"cell-{row_index}-{col_index}", fontName=bold if row_index == 0 else regular,
            fontSize=7.5, leading=9, textColor=colors.HexColor("#173E34") if row_index == 0 else colors.HexColor("#17202A"),
        )) for col_index, value in enumerate(row)]
        for row_index, row in enumerate(rows)
    ]
    table = Table(normalized, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9F1EE")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D4DED9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table
