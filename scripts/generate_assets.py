"""Generate real compile benchmarks and side-by-side preview PNG for README.md."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from strata.models import load_exam_spec
from strata.pdf_builder import build_exam_pdf, build_solutions_pdf
from strata.runner_builder import build_runner_html
from strata.triage import build_triage_exam, load_attempts, load_problem_banks


def benchmark_compile(
    spec_path: Path, iterations: int = 5, solutions: bool = False
) -> dict[str, float | int]:
    """Measure real wall-clock compile timings and output artifact sizes."""
    spec = load_exam_spec(spec_path)
    pdf_times: list[float] = []
    runner_times: list[float] = []
    pdf_bytes = 0
    runner_bytes = 0
    exam_pages = 0
    key_pages = 0

    for _ in range(iterations):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            assets = tmp_dir / "assets"
            t0 = time.perf_counter()
            pdf_res = build_exam_pdf(
                spec=spec,
                output_pdf=tmp_dir / "exam.pdf",
                spec_dir=spec_path.parent,
                assets_dir=assets,
            )
            if solutions:
                build_solutions_pdf(
                    spec=spec,
                    output_pdf=tmp_dir / "solutions.pdf",
                    spec_dir=spec_path.parent,
                    assets_dir=assets,
                )
            t1 = time.perf_counter()
            runner_path = build_runner_html(
                spec=spec,
                pdf_result=pdf_res,
                output_html=tmp_dir / "runner.html",
            )
            t2 = time.perf_counter()

            pdf_times.append((t1 - t0) * 1000.0)
            runner_times.append((t2 - t1) * 1000.0)
            pdf_bytes = pdf_res.pdf_path.stat().st_size
            runner_bytes = runner_path.stat().st_size
            exam_pages = pdf_res.exam_pages
            key_pages = pdf_res.key_pages

    totals = [p + r for p, r in zip(pdf_times, runner_times, strict=True)]
    return {
        "problems": len(spec.all_problems()),
        "exam_pages": exam_pages,
        "key_pages": key_pages,
        "pdf_median_ms": round(statistics.median(pdf_times), 1),
        "runner_median_ms": round(statistics.median(runner_times), 1),
        "total_median_ms": round(statistics.median(totals), 1),
        "pdf_kb": round(pdf_bytes / 1024.0, 1),
        "runner_kb": round(runner_bytes / 1024.0, 1),
    }


def generate_preview_png(out_png: Path) -> None:
    """Render side-by-side preview of the 2-column CS exam and single-column Calculus exam."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        p1 = load_exam_spec("examples/systems_algorithms.yaml")
        r1 = build_exam_pdf(p1, tmp_dir / "sys.pdf", Path("examples"), tmp_dir / "a1")

        p2 = load_exam_spec("examples/calculus_optimization.yaml")
        r2 = build_exam_pdf(p2, tmp_dir / "calc.pdf", Path("examples"), tmp_dir / "a2")

        with pymupdf.open(r1.pdf_path) as d1:
            pix1 = d1[0].get_pixmap(dpi=130)
            img1 = Image.frombytes("RGB", [pix1.width, pix1.height], pix1.samples)
        with pymupdf.open(r2.pdf_path) as d2:
            pix2 = d2[0].get_pixmap(dpi=130)
            img2 = Image.frombytes("RGB", [pix2.width, pix2.height], pix2.samples)

    pad = 32
    header_h = 44
    w = img1.width + img2.width + pad * 3
    h = max(img1.height, img2.height) + pad * 2 + header_h

    canvas = Image.new("RGB", (w, h), (10, 10, 10))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text(
        (pad, 14),
        "Two-Column MCQ + Fill-Blank (systems_algorithms.yaml)",
        fill=(56, 189, 248),
        font=font,
    )
    draw.text(
        (pad * 2 + img1.width, 14),
        "Single-Column Math + Mathtext (calculus_optimization.yaml)",
        fill=(56, 189, 248),
        font=font,
    )

    canvas.paste(img1, (pad, pad + header_h))
    canvas.paste(img2, (pad * 2 + img1.width, pad + header_h))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png)


def main() -> None:
    sys_bench = benchmark_compile(
        Path("examples/systems_algorithms.yaml"), iterations=5, solutions=True
    )
    calc_bench = benchmark_compile(
        Path("examples/calculus_optimization.yaml"), iterations=5, solutions=False
    )

    bank = load_problem_banks(
        [Path("examples/calculus_optimization.yaml"), Path("examples/systems_algorithms.yaml")]
    )
    attempts = load_attempts([Path("examples/attempts")])
    _, report = build_triage_exam(bank, attempts, target_minutes=15)

    preview_path = Path("docs/assets/preview.png")
    generate_preview_png(preview_path)

    results = {
        "systems_algorithms_2col_solutions": sys_bench,
        "calculus_optimization_1col_mathtext": calc_bench,
        "triage_demo": {
            "bank_problems": report.total_bank_problems,
            "attempts_loaded": report.total_attempts_loaded,
            "selected_problems": report.selected_problem_ids,
            "estimated_minutes": report.estimated_minutes,
            "mastered_tags": [m.tag for m in report.mastered_tags],
            "active_tags": [m.tag for m in report.active_tags],
        },
        "preview_png": str(preview_path),
    }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
