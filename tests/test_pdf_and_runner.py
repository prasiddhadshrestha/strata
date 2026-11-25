"""Tests for ReportLab PDF compilation, solutions guide, and air-gapped HTML runner."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from strata.models import CropSpec, ExamSpec, ProblemSpec, load_exam_spec
from strata.pdf_builder import build_exam_pdf, build_solutions_pdf, sanitize_reportlab_text
from strata.runner_builder import build_runner_html


def test_sanitize_reportlab_text_preserves_tags_and_converts_backticks() -> None:
    raw = "Check <b>value</b> when `lo <= hi` & x < 5."
    cleaned = sanitize_reportlab_text(raw)
    assert "<b>value</b>" in cleaned
    assert '<font name="Courier">lo &lt;= hi</font>' in cleaned
    assert "&amp; x &lt; 5" in cleaned


def test_single_column_math_exam_and_runner_invariants(tmp_path: Path) -> None:
    spec_path = Path("examples/calculus_optimization.yaml")
    spec = load_exam_spec(spec_path)

    res = build_exam_pdf(
        spec=spec,
        output_pdf=tmp_path / "calc.pdf",
        spec_dir=spec_path.parent,
        assets_dir=tmp_path / "assets",
        include_key=True,
    )
    assert res.pdf_path.exists()
    assert res.exam_pages >= 1
    assert res.key_pages >= 1
    assert res.total_pages == res.exam_pages + res.key_pages

    runner = build_runner_html(spec=spec, pdf_result=res, output_html=tmp_path / "calc_runner.html")
    html_text = runner.read_text(encoding="utf-8")

    # Zero external CDNs (air-gapped invariant)
    assert "http://" not in html_text
    assert "https://" not in html_text
    # Regression checks: wall-clock timer, ungraded default, spoiler-locked answers
    assert "deadlineMs = Date.now()" in html_text
    assert 'status: "ungraded"' in html_text
    assert "keyRevealed" in html_text
    assert "AudioContext" in html_text
    assert "exportAttempt" in html_text
    assert html_text.count('alt="Exam Page ') == res.exam_pages
    assert html_text.count('alt="Answer Key Page ') == res.key_pages


def test_two_column_exam_solutions_and_no_key(tmp_path: Path) -> None:
    spec_path = Path("examples/systems_algorithms.yaml")
    spec = load_exam_spec(spec_path)

    res_with_key = build_exam_pdf(
        spec=spec,
        output_pdf=tmp_path / "sys.pdf",
        spec_dir=spec_path.parent,
        assets_dir=tmp_path / "assets",
        include_key=True,
    )
    assert res_with_key.key_pages >= 1

    res_no_key = build_exam_pdf(
        spec=spec,
        output_pdf=tmp_path / "sys_nokey.pdf",
        spec_dir=spec_path.parent,
        assets_dir=tmp_path / "assets",
        include_key=False,
    )
    assert res_no_key.key_pages == 0
    runner_no_key = build_runner_html(
        spec=spec, pdf_result=res_no_key, output_html=tmp_path / "sys_nokey_runner.html"
    )
    assert "HAS_KEY_PAGES = false;" in runner_no_key.read_text(encoding="utf-8")

    solutions_pdf = build_solutions_pdf(
        spec=spec,
        output_pdf=tmp_path / "sys_solutions.pdf",
        spec_dir=spec_path.parent,
        assets_dir=tmp_path / "assets",
    )
    assert solutions_pdf.exists()
    assert solutions_pdf.stat().st_size > 2000


def test_tall_workspace_and_inline_crop_compile_cleanly(tmp_path: Path) -> None:
    fig_pdf = tmp_path / "diagrams.pdf"
    c = canvas.Canvas(str(fig_pdf), pagesize=letter)
    c.line(80, 500, 320, 620)
    c.showPage()
    c.save()

    spec = ExamSpec(
        title="Cropped Diagram & Tall Space Test",
        problems=[
            ProblemSpec(
                id="fig-1",
                prompt="Analyze `slope(x)` when x < 4 & y > 0.",
                crop=CropSpec(source_pdf=str(fig_pdf), page=0, bbox=(0.05, 0.1, 0.6, 0.5)),
                space_inches=6.5,
                answer="Positive constant slope",
            )
        ],
    )
    res = build_exam_pdf(
        spec=spec,
        output_pdf=tmp_path / "out.pdf",
        spec_dir=tmp_path,
        assets_dir=tmp_path / "assets",
    )
    assert res.pdf_path.exists()
    assert (tmp_path / "assets" / "crop_fig-1.png").exists()
