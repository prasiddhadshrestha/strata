"""Typer CLI entrypoint for Strata."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from strata import __version__
from strata.crop import crop_pdf_region
from strata.models import LayoutMode, format_points, load_exam_spec, save_exam_spec
from strata.pdf_builder import build_exam_pdf, build_solutions_pdf
from strata.runner_builder import build_runner_html
from strata.triage import build_triage_exam, load_attempts, load_problem_banks

app = typer.Typer(
    name="strata",
    help="Declarative assessment compiler for print PDFs, timed runners, and mastery triage.",
    no_args_is_help=True,
)


def _compile_spec(
    spec_path: Path,
    out_dir: Path,
    layout: LayoutMode | None = None,
    include_key: bool | None = None,
    solutions: bool = False,
) -> tuple[Path, Path, Path | None, int, int]:
    """Compile an ExamSpec into print PDF, offline timed runner, and optional solutions PDF."""
    spec = load_exam_spec(spec_path)
    if layout is not None:
        spec = spec.model_copy(update={"layout": layout})

    stem = spec_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"

    pdf_path = out_dir / f"{stem}.pdf"
    runner_path = out_dir / f"{stem}_runner.html"

    pdf_res = build_exam_pdf(
        spec=spec,
        output_pdf=pdf_path,
        spec_dir=spec_path.parent,
        assets_dir=assets_dir,
        include_key=include_key,
    )
    build_runner_html(spec=spec, pdf_result=pdf_res, output_html=runner_path)

    solutions_path: Path | None = None
    if solutions:
        solutions_path = out_dir / f"{stem}_solutions.pdf"
        build_solutions_pdf(
            spec=spec,
            output_pdf=solutions_path,
            spec_dir=spec_path.parent,
            assets_dir=assets_dir,
        )

    return pdf_res.pdf_path, runner_path, solutions_path, pdf_res.exam_pages, pdf_res.key_pages


@app.command()
def version() -> None:
    """Print the installed Strata version."""
    typer.echo(f"strata {__version__}")


@app.command()
def validate(spec: Path = typer.Argument(..., exists=True, help="Path to exam YAML spec.")) -> None:
    """Validate an exam YAML manifest and print point/tag summary."""
    exam = load_exam_spec(spec)
    probs = exam.all_problems()
    tags = sorted({t for p in probs for t in p.tags})
    typer.echo(f"VALID: {exam.title}")
    typer.echo(
        f"Problems: {len(probs)} | Total Points: {format_points(exam.total_points or 0.0)} | "
        f"Duration: {exam.duration_minutes}m | Layout: {exam.layout}"
    )
    typer.echo(f"Tags ({len(tags)}): {', '.join(tags) if tags else 'none'}")


@app.command()
def crop(
    source_pdf: Path = typer.Argument(..., exists=True, help="Source PDF path."),
    page: int = typer.Option(0, "--page", "-p", min=0, help="Zero-based page index."),
    bbox: str = typer.Option(
        ..., "--bbox", "-b", help="Normalized crop box as 'x0,y0,x1,y1' in [0, 1]."
    ),
    out: Path = typer.Option(..., "--out", "-o", help="Output PNG path."),
    dpi: int = typer.Option(200, "--dpi", min=72, max=600, help="Render DPI."),
) -> None:
    """Extract a normalized bounding-box diagram from a PDF page."""
    parts = [float(x.strip()) for x in bbox.split(",")]
    if len(parts) != 4:
        raise typer.BadParameter("bbox must have exactly 4 comma-separated floats: x0,y0,x1,y1")
    out_path = crop_pdf_region(source_pdf, page, (parts[0], parts[1], parts[2], parts[3]), out, dpi)
    typer.echo(f"Cropped diagram saved: {out_path}")


@app.command()
def build(
    spec: Path = typer.Argument(..., exists=True, help="Path to exam YAML spec."),
    out: Path = typer.Option(Path("out"), "--out", "-o", help="Output directory."),
    layout: LayoutMode | None = typer.Option(
        None, "--layout", "-l", help="Override layout: single or two_column."
    ),
    key: bool | None = typer.Option(
        None, "--key/--no-key", help="Include or omit tear-off answer key page."
    ),
    solutions: bool = typer.Option(
        False, "--solutions", help="Also compile annotated solutions PDF."
    ),
) -> None:
    """Compile an exam YAML manifest into a print PDF and offline HTML runner."""
    pdf_path, runner_path, solutions_path, exam_pages, key_pages = _compile_spec(
        spec_path=spec,
        out_dir=out,
        layout=layout,
        include_key=key,
        solutions=solutions,
    )
    typer.echo(f"PDF: {pdf_path} ({exam_pages} exam pages, {key_pages} key pages)")
    typer.echo(f"Runner: {runner_path}")
    if solutions_path:
        typer.echo(f"Solutions: {solutions_path}")


@app.command()
def triage(
    bank: list[Path] = typer.Option(
        ..., "--bank", "-b", help="YAML problem bank file(s) or directory."
    ),
    attempts: list[Path] = typer.Option(
        ..., "--attempts", "-a", help="Attempt JSON file(s) or directory."
    ),
    minutes: int = typer.Option(15, "--minutes", "-m", min=1, max=180, help="Target drill length."),
    out: Path = typer.Option(Path("out/triage"), "--out", "-o", help="Output directory."),
    tags: str | None = typer.Option(
        None, "--tags", "-t", help="Optional comma-separated explicit tags to target."
    ),
    mastery_streak: int = typer.Option(
        2, "--mastery-streak", min=1, max=10, help="Consecutive passes to mark a tag mastered."
    ),
    include_mastered: bool = typer.Option(
        False,
        "--include-mastered",
        help="Fallback to mastered tags if all active weak tags are cleared.",
    ),
    solutions: bool = typer.Option(False, "--solutions", help="Also emit annotated solutions PDF."),
) -> None:
    """Build a targeted review exam from attempt telemetry and active weak tags."""
    bank_problems = load_problem_banks(bank)
    attempt_records = load_attempts(attempts)
    explicit = [t.strip() for t in tags.split(",")] if tags else None

    triage_spec, report = build_triage_exam(
        bank_problems=bank_problems,
        attempts=attempt_records,
        target_minutes=minutes,
        mastery_streak=mastery_streak,
        explicit_tags=explicit,
        include_mastered=include_mastered,
    )

    out.mkdir(parents=True, exist_ok=True)
    spec_file = out / "triage_spec.yaml"
    save_exam_spec(triage_spec, spec_file)

    report_file = out / "triage_report.json"
    report_payload = {
        "total_bank_problems": report.total_bank_problems,
        "total_attempts_loaded": report.total_attempts_loaded,
        "estimated_minutes": report.estimated_minutes,
        "selected_problem_ids": report.selected_problem_ids,
        "active_tags": [
            {
                "tag": m.tag,
                "misses": m.misses,
                "partials": m.partials,
                "pass_streak": m.pass_streak,
                "priority_score": m.priority_score,
            }
            for m in report.active_tags
        ],
        "mastered_tags": [m.tag for m in report.mastered_tags],
    }
    report_file.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")

    pdf_path, runner_path, solutions_path, exam_pages, key_pages = _compile_spec(
        spec_path=spec_file,
        out_dir=out,
        solutions=solutions,
    )

    typer.echo(
        f"Triage Packed: {len(report.selected_problem_ids)} problems (~{report.estimated_minutes}m)"
    )
    typer.echo(f"Active Tags: {', '.join(m.tag for m in report.active_tags) or 'none'}")
    typer.echo(f"Mastered Tags: {', '.join(m.tag for m in report.mastered_tags) or 'none'}")
    typer.echo(f"PDF: {pdf_path} ({exam_pages} exam pages, {key_pages} key pages)")
    typer.echo(f"Runner: {runner_path}")
    if solutions_path:
        typer.echo(f"Solutions: {solutions_path}")
    typer.echo(f"Report: {report_file}")


if __name__ == "__main__":
    app()
