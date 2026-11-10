from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from PIL import Image

from app.analysis import AnalysisError
from app.workbench_models import (
    DIAGRAM_LIMIT, SOURCE_LABELS, Diagram, criterion_status, decode_file,
    question_resolved, rule_confirmed,
)


WORD_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEMPLATE_VERSION = "general-ba-v1"


def document_scope(record: dict, function_ids: list[str], *, dispatch: bool = False) -> dict:
    if record["review"] is None:
        raise AnalysisError(409, "REVIEW_REQUIRED", "請先儲存 BA 審閱內容。")
    by_function = {item["id"]: item for item in record["functions"]}
    if len(set(function_ids)) != len(function_ids) or any(key not in by_function for key in function_ids):
        raise AnalysisError(422, "INVALID_SCOPE", "請重新選擇本次處理的功能。")
    functions = [by_function[key] for key in function_ids]
    if any(not item["rule_ids"] for item in functions):
        raise AnalysisError(422, "FUNCTION_RULE_REQUIRED", "請為所選功能關聯至少一條業務規則。")

    review = record["review"]
    selected = {key for item in functions for key in item["rule_ids"]}
    rules = {item["id"]: item for item in review["rules"] if item["id"] in selected}
    criteria = [item for item in review["criteria"] if item["ruleId"] in rules]
    blockers = {key for item in criteria for key in item["blockerIds"]}
    questions = {item["id"]: item for item in review["questions"] if item["id"] in blockers
                 or selected.intersection(item["ruleIds"])}

    if dispatch:
        if not record["grade"]:
            raise AnalysisError(409, "GRADE_REQUIRED", "請先儲存需求等級。")
        for rule in rules.values():
            if not rule_confirmed(rule):
                raise AnalysisError(409, "RULE_PENDING", f"{rule['id']} 仍待業務確認，請調整派發範圍。")
        if {item["ruleId"] for item in criteria} != selected:
            raise AnalysisError(409, "CRITERION_REQUIRED", "請為派發範圍內的每條規則補上驗收條件。")
        for item in criteria:
            if criterion_status(item, rules, questions) != "可供驗收設計":
                raise AnalysisError(409, "CRITERION_PENDING", f"{item['id']} 尚待確認或補齊，請調整派發範圍。")
    return {"functions": functions, "rules": rules, "questions": questions, "criteria": criteria}


def document_content(record: dict, scope: dict, version: int | None, team: str | None) -> dict:
    rules, questions, criteria = scope["rules"], scope["questions"], scope["criteria"]
    source = record["source"]
    review = record["review"]
    fields = [
        ["需求單號", record["number"]],
        ["系統名稱", record["system_name"] or "待填"],
        ["需求等級", record["grade"] or "待分級"],
        ["來源", f"{SOURCE_LABELS[source['kind']]} {source['number']} {source['name']}"],
        ["來源版次", source["version"]],
        ["BA 文件版次", f"v{version}" if version is not None else "預覽"],
        ["用途", f"派發給 {team}" if team else "BA 審閱草稿"],
    ]
    rule_lines = []
    for item in rules.values():
        status = "業務已確認" if rule_confirmed(item) else "待確認"
        rule_lines.append(f"{item['id']}　{item['text'] or '待補'}（{status}）")
        if item["confirmation"]:
            rule_lines.append("　確認依據　" + item["confirmation"])
    question_lines = []
    for item in questions.values():
        resolved = question_resolved(item)
        status = "已確認" if resolved else "待確認"
        question_lines.append(f"{item['id']}　{item['text'] or '待補'}（{status}）")
        if item["decision"]:
            label = "業務決議" if resolved else "決議草稿"
            question_lines.append(f"　{label}　{item['decision']}")
        if resolved:
            question_lines.append(f"　確認人與日期　{item['confirmedBy']}　{item['confirmedOn']}")
    criterion_lines = []
    for item in criteria:
        criterion_lines.extend([
            f"{item['id']}　{item['ruleId']}（{criterion_status(item, rules, questions)}）",
            "　前置條件　" + (item["given"] or "待補"),
            "　操作　" + (item["when"] or "待補"),
            "　預期結果　" + (item["then"] or "待補"),
        ])
        if item["blockerIds"]:
            criterion_lines.append("　關聯問題　" + "、".join(item["blockerIds"]))
    citations = []
    for feature in record["analysis"]["data"]["features"]:
        key = "BR-" + feature["id"][1:]
        if key not in rules:
            continue
        location = rules[key]["location"]
        label = f"{key}　{location}　" if location else f"{key}　"
        citations.extend(label + quote for quote in feature["source_quotes"])
    return {
        "title": record["title"],
        "fields": fields,
        "sections": [
            ["業務目標與範圍", ["目標　" + (review["goal"] or "待補"), "範圍　" + (review["scope"] or "待補")]],
            ["功能範圍", [f"{item['id']}　{item['title']}（{'、'.join(item['rule_ids'])}）" for item in scope["functions"]]],
            ["業務規則", rule_lines],
            ["待確認事項與決議", question_lines or ["無"]],
            ["驗收條件", criterion_lines or ["待補"]],
            ["來源摘錄", citations or ["由 BA 補充來源"]],
        ],
    }


def diagram_image(diagram: Diagram | None, scope: dict) -> tuple[str, BytesIO, tuple[int, int]] | None:
    if diagram is None:
        return None
    allowed = set(scope["rules"]) | {item["id"] for item in scope["criteria"]}
    if diagram.id not in allowed:
        raise AnalysisError(422, "DIAGRAM_OUT_OF_SCOPE", "請選擇本次功能範圍內的活動圖。")
    image_bytes = decode_file(diagram.png, DIAGRAM_LIMIT)
    try:
        with Image.open(BytesIO(image_bytes), formats=["PNG"]) as image:
            width, height = image.size
            if not (1 <= width <= 1600 and 1 <= height <= 2000) or getattr(image, "n_frames", 1) != 1:
                raise ValueError("invalid PNG dimensions")
            image.verify()
        with Image.open(BytesIO(image_bytes), formats=["PNG"]) as image:
            result = BytesIO()
            image.convert("RGB").save(result, format="PNG")
            result.seek(0)
        return diagram.id, result, (width, height)
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise AnalysisError(422, "INVALID_DIAGRAM", "活動圖必須是 1600 × 2000 以內的 PNG。") from exc


def render_word(content: dict, diagram: tuple[str, BytesIO, tuple[int, int]] | None) -> bytes:
    document = Document()
    page = document.sections[0]
    page.page_width, page.page_height = Mm(210), Mm(297)
    page.left_margin = page.right_margin = Mm(20)
    page.top_margin = page.bottom_margin = Mm(18)
    for name, size in (("Title", 22), ("Heading 1", 14), ("Normal", 11)):
        style = document.styles[name]
        style.font.name = "Microsoft JhengHei"
        style.font.color.rgb = RGBColor(25, 39, 53)
        style.font.size = Pt(size)
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft JhengHei")
    title_properties = document.styles["Title"].element.pPr
    if title_properties is not None:
        border = title_properties.find(qn("w:pBdr"))
        if border is not None:
            title_properties.remove(border)
    document.styles["Title"].paragraph_format.space_after = Pt(8)
    document.styles["Heading 1"].paragraph_format.space_before = Pt(10)
    document.styles["Heading 1"].paragraph_format.space_after = Pt(4)
    document.styles["Normal"].paragraph_format.space_after = Pt(3)
    document.core_properties.title = "BA 需求文件"
    document.core_properties.author = "需求分析助手"

    document.add_heading("BA 需求文件", level=0)
    document.add_paragraph(content["title"])
    table = document.add_table(rows=0, cols=2)
    table.style = "Light Shading Accent 1"
    table.autofit = False
    table.columns[0].width = Mm(35)
    table.columns[1].width = Mm(135)
    for label, value in content["fields"]:
        left, right = table.add_row().cells
        left.width, right.width = Mm(35), Mm(135)
        left.text, right.text = label, value

    for heading, lines in content["sections"]:
        document.add_heading(heading, level=1)
        for line in lines:
            document.add_paragraph(line)
    if diagram is not None:
        _, stream, (width, height) = diagram
        heading = document.add_heading("UML 活動圖", level=1)
        heading.paragraph_format.keep_with_next = True
        document.add_picture(stream, width=Mm(min(165, 195 * width / height)))
        document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
