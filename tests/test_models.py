"""Tests for Pydantic v2 exam schemas and validation boundaries."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from strata.models import CropSpec, ExamSpec, ProblemSpec, format_points, load_exam_spec


def test_format_points() -> None:
    assert format_points(5.0) == "5"
    assert format_points(2.5) == "2.5"
    assert format_points(10.25) == "10.25"


def test_top_level_problems_normalize_into_single_section() -> None:
    spec = ExamSpec(
        title="Quick Check",
        problems=[
            ProblemSpec(
                id="q1",
                prompt="Compute 2 + 2.",
                points=4,
                tags=["Arithmetic ", "arithmetic"],
            ),
            ProblemSpec(id="q2", prompt="State big-O.", points=6),
        ],
    )
    assert len(spec.sections) == 1
    assert spec.total_points == 10.0
    assert spec.all_problems()[0].tags == ["arithmetic"]


def test_duplicate_problem_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="Duplicate problem id"):
        ExamSpec(
            title="Bad Spec",
            problems=[
                ProblemSpec(id="dup", prompt="One"),
                ProblemSpec(id="dup", prompt="Two"),
            ],
        )


def test_mismatched_total_points_rejected() -> None:
    with pytest.raises(ValidationError, match="total_points"):
        ExamSpec(
            title="Bad Points",
            total_points=99,
            problems=[ProblemSpec(id="q1", prompt="Test", points=5)],
        )


def test_mcq_requires_at_least_two_options() -> None:
    with pytest.raises(ValidationError, match="at least 2 options"):
        ProblemSpec(id="m1", type="mcq", prompt="Pick one", options=["Only one"])


def test_crop_bbox_validation() -> None:
    with pytest.raises(ValidationError):
        CropSpec(source_pdf="a.pdf", page=0, bbox=(0.6, 0.2, 0.4, 0.8))
    with pytest.raises(ValidationError):
        CropSpec(source_pdf="a.pdf", page=0, bbox=(-0.1, 0.2, 0.4, 0.8))


def test_example_specs_load_and_sections_match_types() -> None:
    sys_spec = load_exam_spec("examples/systems_algorithms.yaml")
    assert all(p.type == "mcq" for p in sys_spec.sections[0].problems)
    assert all(p.type == "fill_blank" for p in sys_spec.sections[1].problems)

    calc_spec = load_exam_spec("examples/calculus_optimization.yaml")
    assert len(calc_spec.all_problems()) == 4
