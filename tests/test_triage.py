"""Tests for stateless tag scoring, mastery streaks, relative asset resolution, and packing."""

from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from strata.models import (
    AttemptProblemResult,
    AttemptRecord,
    CropSpec,
    ExamSpec,
    ProblemSpec,
    save_exam_spec,
)
from strata.pdf_builder import build_exam_pdf
from strata.triage import build_triage_exam, load_attempts, load_problem_banks, score_tags


def test_mastery_streak_clears_boundary_condition() -> None:
    attempts = load_attempts([Path("examples/attempts")])
    tag_map, _, _ = score_tags(attempts, mastery_streak=2)

    assert tag_map["boundary-condition"].pass_streak == 2
    assert tag_map["boundary-condition"].mastered is True
    assert tag_map["boundary-condition"].priority_score == 0.0

    assert tag_map["sign-flip"].misses == 2
    assert tag_map["sign-flip"].mastered is False
    assert tag_map["sign-flip"].priority_score > 0.0


def test_triage_packs_active_tags_within_budget() -> None:
    bank = load_problem_banks(
        [Path("examples/calculus_optimization.yaml"), Path("examples/systems_algorithms.yaml")]
    )
    attempts = load_attempts([Path("examples/attempts")])

    spec, report = build_triage_exam(bank, attempts, target_minutes=12, mastery_streak=2)
    assert report.estimated_minutes <= 12.0
    assert "calc-03" in report.selected_problem_ids
    assert all(p.id in report.selected_problem_ids for p in spec.all_problems())


def test_triage_explicit_tags_override() -> None:
    bank = load_problem_banks([Path("examples/systems_algorithms.yaml")])
    attempts = load_attempts([Path("examples/attempts")])

    spec, report = build_triage_exam(
        bank, attempts, target_minutes=10, explicit_tags=["boundary-condition"]
    )
    assert "sys-02" in report.selected_problem_ids
    assert len(spec.all_problems()) >= 1


def test_triage_errors_when_no_active_tags() -> None:
    bank = load_problem_banks([Path("examples/systems_algorithms.yaml")])
    with pytest.raises(ValueError, match="No active unmastered tags"):
        build_triage_exam(bank, attempts=[], target_minutes=10)


def test_triage_preserves_relative_crop_paths_across_directories(tmp_path: Path) -> None:
    bank_dir = tmp_path / "bank"
    out_dir = tmp_path / "nested" / "triage_out"
    bank_dir.mkdir(parents=True)

    fig_pdf = bank_dir / "diagram.pdf"
    c = canvas.Canvas(str(fig_pdf), pagesize=letter)
    c.rect(50, 500, 150, 100, fill=1)
    c.showPage()
    c.save()

    bank_spec = ExamSpec(
        title="Bank With Relative Diagram",
        problems=[
            ProblemSpec(
                id="diag-1",
                prompt="Interpret the region.",
                crop=CropSpec(source_pdf="diagram.pdf", page=0, bbox=(0.05, 0.1, 0.5, 0.5)),
                tags=["geometry"],
            )
        ],
    )
    bank_yaml = save_exam_spec(bank_spec, bank_dir / "bank.yaml")
    loaded = load_problem_banks([bank_yaml])

    attempt = AttemptRecord(
        exam_title="Baseline",
        timestamp="2026-11-24T12:00:00Z",
        results=[AttemptProblemResult(problem_id="diag-1", status="missed", tags=["geometry"])],
    )
    triage_spec, _ = build_triage_exam(loaded, [attempt], target_minutes=10)
    triage_yaml = save_exam_spec(triage_spec, out_dir / "triage_spec.yaml")

    pdf_res = build_exam_pdf(
        spec=triage_spec,
        output_pdf=out_dir / "triage.pdf",
        spec_dir=triage_yaml.parent,
        assets_dir=out_dir / "assets",
    )
    assert pdf_res.pdf_path.exists()
