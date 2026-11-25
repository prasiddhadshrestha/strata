"""ReportLab PDF compiler for print exams, tear-off answer keys, and annotated solutions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from strata.crop import crop_pdf_region
from strata.math_render import render_mathtext_png, split_prompt_and_display_math
from strata.models import ExamSpec, ProblemSpec, format_points

_ALLOWED_TAG_RE = re.compile(r"</?(?:b|i|u|sup|sub|br\s*/?|font(?:\s+[^>]*)?|code)>")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_ENTITY_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|nbsp);)")


@dataclass(frozen=True)
class PdfBuildResult:
    """Metadata returned after compiling an exam PDF."""

    pdf_path: Path
    total_pages: int
    exam_pages: int
    key_pages: int


class _SectionSentinel(Flowable):
    """Zero-height marker flowable used to record the exact page where the answer key begins."""

    def __init__(self, marker_name: str) -> None:
        super().__init__()
        self.marker_name = marker_name
        self.width = 0
        self.height = 0

    def draw(self) -> None:
        return None


class _StrataDocTemplate(BaseDocTemplate):
    """ReportLab DocTemplate that tracks exact page index of the tear-off answer key."""

    def __init__(self, filename: str, **kwargs: object) -> None:
        super().__init__(filename, **kwargs)
        self.key_start_page: int | None = None
        self.rendered_pages: int = 0

    def afterPage(self) -> None:
        self.rendered_pages = max(self.rendered_pages, self.page)

    def afterFlowable(self, flowable: Flowable) -> None:
        if isinstance(flowable, _SectionSentinel) and flowable.marker_name == "answer_key":
            self.key_start_page = self.page


def sanitize_reportlab_text(text: str) -> str:
    """Escape bare XML chars outside allowed tags and convert inline `code` to Courier."""
    placeholders: list[str] = []

    def _stash_tag(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"@@TAG{len(placeholders) - 1}@@"

    def _stash_code(match: re.Match[str]) -> str:
        inner = match.group(1)
        inner = _ENTITY_RE.sub("&amp;", inner)
        inner = inner.replace("<", "&lt;").replace(">", "&gt;")
        placeholders.append(f'<font name="Courier">{inner}</font>')
        return f"@@TAG{len(placeholders) - 1}@@"

    stashed = _ALLOWED_TAG_RE.sub(_stash_tag, text)
    stashed = _BACKTICK_RE.sub(_stash_code, stashed)
    stashed = _ENTITY_RE.sub("&amp;", stashed)
    stashed = stashed.replace("<", "&lt;").replace(">", "&gt;")

    for idx, tag in enumerate(placeholders):
        stashed = stashed.replace(f"@@TAG{idx}@@", tag)
    return stashed


def _scaled_image(
    img_path: Path,
    target_width_in: float,
    max_width_pts: float,
    max_height_pts: float = 4.2 * inch,
) -> Image:
    """Create an aspect-ratio-preserving ReportLab Image clamped to the enclosing frame."""
    with PILImage.open(img_path) as pil_img:
        px_w, px_h = pil_img.size

    if px_w <= 0 or px_h <= 0:
        raise ValueError(f"Invalid image dimensions for {img_path}")

    aspect = px_h / float(px_w)
    width_pts = min(target_width_in * inch, max_width_pts)
    height_pts = width_pts * aspect
    if height_pts > max_height_pts:
        height_pts = max_height_pts
        width_pts = height_pts / aspect

    return Image(str(img_path), width=width_pts, height=height_pts)


def _draw_page_footer(canvas, doc) -> None:
    """Draw minimal page number footer on every page."""
    canvas.saveState()
    canvas.setFont("Times-Roman", 8.5)
    canvas.setFillColor(colors.HexColor("#475569"))
    canvas.drawRightString(letter[0] - 0.55 * inch, 0.35 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _build_doc(pdf_path: Path, layout: str) -> tuple[_StrataDocTemplate, float]:
    """Create a 1-column or 2-column BaseDocTemplate and return (doc, usable_col_width_pts)."""
    page_w, page_h = letter
    margin_x = 0.65 * inch if layout == "single" else 0.5 * inch
    margin_y = 0.55 * inch

    doc = _StrataDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=margin_x,
        rightMargin=margin_x,
        topMargin=margin_y,
        bottomMargin=margin_y,
    )

    usable_w = page_w - 2 * margin_x
    usable_h = page_h - 2 * margin_y

    if layout == "two_column":
        gutter = 0.25 * inch
        col_w = (usable_w - gutter) / 2.0
        frames = [
            Frame(margin_x, margin_y, col_w, usable_h, id="col1", topPadding=0, bottomPadding=0),
            Frame(
                margin_x + col_w + gutter,
                margin_y,
                col_w,
                usable_h,
                id="col2",
                topPadding=0,
                bottomPadding=0,
            ),
        ]
        max_col_w = col_w - 0.1 * inch
    else:
        frames = [
            Frame(
                margin_x,
                margin_y,
                usable_w,
                usable_h,
                id="single",
                topPadding=0,
                bottomPadding=0,
            )
        ]
        max_col_w = usable_w - 0.1 * inch

    doc.addPageTemplates([PageTemplate(id="main", frames=frames, onPage=_draw_page_footer)])
    return doc, max_col_w


def _make_styles(compact: bool) -> dict[str, ParagraphStyle]:
    """Build cohesive ReportLab paragraph styles tuned for print legibility."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "StrataTitle",
            parent=base["Heading1"],
            fontName="Times-Bold",
            fontSize=11.5 if compact else 13.5,
            leading=14.5 if compact else 17,
            textColor=colors.HexColor("#0f172a"),
            spaceAfter=2,
        ),
        "hdr_left": ParagraphStyle(
            "StrataHdrLeft",
            parent=base["Normal"],
            fontName="Times-Bold",
            fontSize=11.5,
            leading=14,
            alignment=0,
            textColor=colors.HexColor("#0f172a"),
        ),
        "hdr_center": ParagraphStyle(
            "StrataHdrCenter",
            parent=base["Normal"],
            fontName="Times-Bold",
            fontSize=11.5,
            leading=14,
            alignment=1,
            textColor=colors.HexColor("#0f172a"),
        ),
        "hdr_right": ParagraphStyle(
            "StrataHdrRight",
            parent=base["Normal"],
            fontName="Times-Bold",
            fontSize=11.5,
            leading=14,
            alignment=2,
            textColor=colors.HexColor("#0f172a"),
        ),
        "meta": ParagraphStyle(
            "StrataMeta",
            parent=base["Normal"],
            fontName="Times-Italic",
            fontSize=8.5 if compact else 10,
            leading=11 if compact else 13.5,
            textColor=colors.HexColor("#334155"),
            spaceAfter=4,
        ),
        "section": ParagraphStyle(
            "StrataSection",
            parent=base["Heading2"],
            fontName="Times-Bold",
            fontSize=10 if compact else 11.5,
            leading=13 if compact else 15,
            textColor=colors.HexColor("#0f172a"),
            spaceBefore=6,
            spaceAfter=3,
        ),
        "prompt": ParagraphStyle(
            "StrataPrompt",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=8.5 if compact else 11,
            leading=11.5 if compact else 15,
            textColor=colors.HexColor("#0f172a"),
            spaceAfter=3,
        ),
        "option": ParagraphStyle(
            "StrataOption",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=8 if compact else 10,
            leading=10.5 if compact else 13.5,
            leftIndent=12,
            textColor=colors.HexColor("#1e293b"),
            spaceAfter=1.5,
        ),
        "key_item": ParagraphStyle(
            "StrataKeyItem",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=8.5 if compact else 10,
            leading=11.5 if compact else 13.5,
            textColor=colors.HexColor("#1e293b"),
            spaceAfter=4,
        ),
        "notes": ParagraphStyle(
            "StrataNotes",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=8.5,
            leading=11.5,
            leftIndent=12,
            textColor=colors.HexColor("#1e293b"),
            spaceAfter=5,
        ),
    }


def _resolve_problem_assets(
    prob: ProblemSpec,
    spec_dir: Path,
    assets_dir: Path,
    max_col_w: float,
) -> list[Flowable]:
    """Render any math expressions and embedded/cropped figures for a single problem."""
    flowables: list[Flowable] = []
    _, inline_blocks = split_prompt_and_display_math(prob.prompt)
    math_exprs: list[str] = list(inline_blocks)
    if prob.math:
        math_exprs.append(prob.math)

    for expr in math_exprs:
        png_path, w_in, _ = render_mathtext_png(expr, assets_dir / "math", dpi=300, fontsize=11)
        flowables.append(Spacer(1, 0.04 * inch))
        img_flow = _scaled_image(png_path, min(w_in, 4.5), max_col_w, max_height_pts=1.8 * inch)
        flowables.append(img_flow)

    fig_path: Path | None = None
    if prob.crop is not None:
        src_pdf = Path(prob.crop.source_pdf)
        if not src_pdf.is_absolute():
            src_pdf = spec_dir / src_pdf
        fig_path = assets_dir / f"crop_{prob.id}.png"
        crop_pdf_region(src_pdf, prob.crop.page, prob.crop.bbox, fig_path, dpi=prob.crop.dpi)
    elif prob.image:
        candidate = Path(prob.image)
        fig_path = candidate if candidate.is_absolute() else (spec_dir / candidate)
        if not fig_path.exists():
            raise FileNotFoundError(f"Problem '{prob.id}' image not found: {fig_path}")

    if fig_path is not None:
        flowables.append(Spacer(1, 0.06 * inch))
        flowables.append(_scaled_image(fig_path, prob.img_width_inches, max_col_w))

    return flowables


def _append_exam_header(
    story: list[Flowable],
    spec: ExamSpec,
    styles: dict[str, ParagraphStyle],
    max_col_w: float,
) -> None:
    """Append title bar, optional name line, rules, and instructions."""
    total_pts = format_points(spec.total_points or 0.0)
    num_probs = len(spec.all_problems())

    if spec.layout == "two_column":
        story.append(Paragraph(f"<b>{sanitize_reportlab_text(spec.title)}</b>", styles["title"]))
        meta_parts = [p for p in (spec.course, spec.date) if p]
        if meta_parts:
            joined = " | ".join(sanitize_reportlab_text(p) for p in meta_parts)
            story.append(Paragraph(f"<b>{joined}</b>", styles["meta"]))
    elif spec.course or spec.date:
        left_p = Paragraph(sanitize_reportlab_text(spec.course or spec.title), styles["hdr_left"])
        mid_txt = sanitize_reportlab_text(spec.title if spec.course else "")
        mid_p = Paragraph(mid_txt, styles["hdr_center"])
        right_p = Paragraph(sanitize_reportlab_text(spec.date), styles["hdr_right"])
        col_widths = [max_col_w * 0.34, max_col_w * 0.42, max_col_w * 0.24]
        header_tbl = Table([[left_p, mid_p, right_p]], colWidths=col_widths)
        header_tbl.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(header_tbl)
    else:
        story.append(Paragraph(f"<b>{sanitize_reportlab_text(spec.title)}</b>", styles["title"]))

    if spec.subtitle:
        story.append(Paragraph(sanitize_reportlab_text(spec.subtitle), styles["meta"]))

    if spec.include_name_line:
        story.append(Spacer(1, 0.05 * inch))
        name_line = (
            "<b>Name:</b> ___________________________"
            if spec.layout == "two_column"
            else "<b>Name:</b> ________________________________________"
        )
        story.append(Paragraph(name_line, styles["prompt"]))

    rules_text = " ".join(spec.rules) if spec.rules else "Show all work for full credit."
    summary_line = (
        f"<i>Duration: {spec.duration_minutes} minutes. {num_probs} problems, "
        f"{total_pts} total points. {sanitize_reportlab_text(rules_text)}</i>"
    )
    story.append(Paragraph(summary_line, styles["meta"]))
    story.append(HRFlowable(width="100%", thickness=1.0, color=colors.black, spaceAfter=8))


def build_exam_pdf(
    spec: ExamSpec,
    output_pdf: str | Path,
    spec_dir: Path,
    assets_dir: Path,
    include_key: bool | None = None,
) -> PdfBuildResult:
    """Compile an ExamSpec into a print-ready PDF with optional tear-off answer key."""
    pdf_path = Path(output_pdf)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    compact = spec.layout == "two_column"
    doc, max_col_w = _build_doc(pdf_path, spec.layout)
    styles = _make_styles(compact=compact)

    story: list[Flowable] = []
    _append_exam_header(story, spec, styles, max_col_w)

    q_num = 1
    for sec in spec.sections:
        if len(spec.sections) > 1 or sec.name != "Problems":
            sec_title = sanitize_reportlab_text(sec.name)
            story.append(Paragraph(f"<b>{sec_title}</b>", styles["section"]))
            if sec.description:
                story.append(Paragraph(sanitize_reportlab_text(sec.description), styles["meta"]))

        for prob in sec.problems:
            block: list[Flowable] = []
            cleaned_prompt, _ = split_prompt_and_display_math(prob.prompt)
            pts_str = format_points(prob.points)
            title_prefix = f"{sanitize_reportlab_text(prob.title)}: " if prob.title else ""
            header_html = (
                f"<b>({q_num}) [{pts_str} pts]</b> {title_prefix}"
                f"{sanitize_reportlab_text(cleaned_prompt)}"
            )
            block.append(Paragraph(header_html, styles["prompt"]))
            block.extend(_resolve_problem_assets(prob, spec_dir, assets_dir, max_col_w))

            trailing_space = 0.08
            if prob.type == "mcq":
                for idx, opt in enumerate(prob.options):
                    letter_lbl = chr(ord("A") + idx)
                    block.append(
                        Paragraph(
                            f"[ {letter_lbl} ] {sanitize_reportlab_text(opt)}",
                            styles["option"],
                        )
                    )
            elif prob.type == "fill_blank":
                if "_" * 4 not in cleaned_prompt:
                    blank_item = "____________________"
                    blanks_line = " &nbsp;&nbsp; ".join(blank_item for _ in range(prob.blanks))
                    block.append(Paragraph(f"Answer: {blanks_line}", styles["option"]))
            else:
                trailing_space = prob.space_inches

            story.append(KeepTogether(block))
            if trailing_space > 0:
                story.append(Spacer(1, trailing_space * inch))
            if prob.page_break:
                story.append(PageBreak())
            q_num += 1

    emit_key = spec.show_answer_key if include_key is None else include_key
    has_key_content = any(p.answer or p.explanation for p in spec.all_problems())
    if emit_key and has_key_content:
        story.append(PageBreak())
        story.append(_SectionSentinel("answer_key"))
        story.append(
            Paragraph("<b>ANSWER KEY (DETACH BEFORE STARTING)</b>", styles["section"])
        )
        story.append(HRFlowable(width="100%", thickness=1.0, color=colors.black, spaceAfter=8))

        for idx, prob in enumerate(spec.all_problems(), start=1):
            ans = sanitize_reportlab_text(prob.answer or "See rubric")
            exp = sanitize_reportlab_text(prob.explanation)
            tags = f" <i>(Tags: {', '.join(prob.tags)})</i>" if prob.tags else ""
            line = f"<b>({idx}) [{prob.id}] {ans}</b>"
            if exp:
                line += f" : {exp}"
            line += tags
            story.append(Paragraph(line, styles["key_item"]))

    doc.build(story)

    total_pages = doc.rendered_pages
    if doc.key_start_page is not None:
        exam_pages = doc.key_start_page - 1
        key_pages = total_pages - exam_pages
    else:
        exam_pages = total_pages
        key_pages = 0

    return PdfBuildResult(
        pdf_path=pdf_path,
        total_pages=total_pages,
        exam_pages=exam_pages,
        key_pages=key_pages,
    )


def build_solutions_pdf(
    spec: ExamSpec,
    output_pdf: str | Path,
    spec_dir: Path,
    assets_dir: Path,
) -> Path:
    """Compile a companion Annotated Solutions PDF with rationale and distractor notes."""
    pdf_path = Path(output_pdf)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    doc, max_col_w = _build_doc(pdf_path, "single")
    styles = _make_styles(compact=False)

    story: list[Flowable] = [
        Paragraph(
            f"<b>{sanitize_reportlab_text(spec.title)} : Annotated Solutions</b>",
            styles["title"],
        ),
        Paragraph(
            "Reference solutions with step rationale, distractor breakdowns, and topic tags.",
            styles["meta"],
        ),
        HRFlowable(width="100%", thickness=1.2, color=colors.HexColor("#0f172a"), spaceAfter=8),
    ]

    if spec.guidelines:
        guide_html = "<b>Guidelines:</b><br/>" + "<br/>".join(
            f"{i}. {sanitize_reportlab_text(item)}"
            for i, item in enumerate(spec.guidelines, start=1)
        )
        story.append(Paragraph(guide_html, styles["key_item"]))
        story.append(
            HRFlowable(
                width="100%", thickness=0.6, color=colors.HexColor("#cbd5e1"), spaceAfter=8
            )
        )

    q_num = 1
    for sec in spec.sections:
        story.append(Paragraph(f"<b>{sanitize_reportlab_text(sec.name)}</b>", styles["section"]))
        for prob in sec.problems:
            block: list[Flowable] = []
            cleaned_prompt, _ = split_prompt_and_display_math(prob.prompt)
            block.append(
                Paragraph(
                    f"<b>({q_num}) [{prob.id}]</b> {sanitize_reportlab_text(cleaned_prompt)}",
                    styles["prompt"],
                )
            )
            block.extend(_resolve_problem_assets(prob, spec_dir, assets_dir, max_col_w))

            for idx, opt in enumerate(prob.options):
                letter_lbl = chr(ord("A") + idx)
                block.append(
                    Paragraph(f"[ {letter_lbl} ] {sanitize_reportlab_text(opt)}", styles["option"])
                )

            note_lines: list[str] = []
            if prob.answer:
                note_lines.append(f"<b>Answer:</b> {sanitize_reportlab_text(prob.answer)}")
            if prob.explanation:
                exp_txt = sanitize_reportlab_text(prob.explanation)
                note_lines.append(f"<b>Rationale:</b> {exp_txt}")
            for opt_key, note in prob.distractor_notes.items():
                note_lines.append(
                    f"<i>Why {sanitize_reportlab_text(opt_key)} fails:</i> "
                    f"{sanitize_reportlab_text(note)}"
                )
            if prob.tags:
                note_lines.append(f"<b>Tags:</b> {', '.join(prob.tags)}")

            if note_lines:
                block.append(Spacer(1, 0.04 * inch))
                block.append(Paragraph("<br/>".join(note_lines), styles["notes"]))

            story.append(KeepTogether(block))
            story.append(Spacer(1, 0.08 * inch))
            q_num += 1

    doc.build(story)
    return pdf_path
