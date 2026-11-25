"""Pydantic v2 schemas for declarative exam manifests, PDF crops, and attempt telemetry."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

ProblemType = Literal["free_response", "mcq", "fill_blank"]
LayoutMode = Literal["single", "two_column"]
AttemptStatus = Literal["pass", "missed", "partial"]


class CropSpec(BaseModel):
    """Normalized bounding-box crop specification for extracting figures from a PDF."""

    source_pdf: str
    page: int = Field(default=0, ge=0)
    bbox: tuple[float, float, float, float]
    dpi: int = Field(default=200, ge=72, le=600)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(
        cls, value: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = value
        if not all(0.0 <= coord <= 1.0 for coord in value):
            raise ValueError("bbox ratios must all lie within [0.0, 1.0]")
        if x0 >= x1 or y0 >= y1:
            raise ValueError("bbox must satisfy x0 < x1 and y0 < y1")
        return value


class ProblemSpec(BaseModel):
    """Single assessment item supporting free-response, MCQ, and fill-in-the-blank."""

    id: str = Field(min_length=1)
    type: ProblemType = "free_response"
    title: str | None = None
    prompt: str = Field(min_length=1)
    math: str | None = None
    points: float = Field(default=5.0, gt=0.0)
    estimated_minutes: float = Field(default=5.0, gt=0.0)
    space_inches: float = Field(default=1.6, ge=0.0, le=8.0)
    image: str | None = None
    crop: CropSpec | None = None
    img_width_inches: float = Field(default=3.0, gt=0.2, le=7.0)
    options: list[str] = Field(default_factory=list)
    blanks: int = Field(default=1, ge=1, le=10)
    answer: str = ""
    explanation: str = ""
    distractor_notes: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    page_break: bool = False

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            slug = raw.strip().lower()
            if slug and slug not in seen:
                seen.add(slug)
                cleaned.append(slug)
        return cleaned

    @model_validator(mode="after")
    def validate_type_requirements(self) -> ProblemSpec:
        if self.type == "mcq" and len(self.options) < 2:
            raise ValueError(f"Problem '{self.id}' has type='mcq' and requires at least 2 options")
        if self.image and self.crop:
            raise ValueError(f"Problem '{self.id}' cannot specify both 'image' and 'crop'")
        return self


class SectionSpec(BaseModel):
    """Logical section grouping multiple problems."""

    name: str = Field(min_length=1)
    description: str = ""
    problems: list[ProblemSpec] = Field(min_length=1)


class ExamSpec(BaseModel):
    """Top-level declarative manifest for an exam or problem bank."""

    title: str = Field(min_length=1)
    subtitle: str = ""
    course: str = ""
    date: str = ""
    duration_minutes: int = Field(default=25, ge=1, le=360)
    total_points: float | None = None
    layout: LayoutMode = "single"
    rules: list[str] = Field(default_factory=list)
    guidelines: list[str] = Field(default_factory=list)
    include_name_line: bool = True
    show_answer_key: bool = True
    sections: list[SectionSpec] = Field(default_factory=list)
    problems: list[ProblemSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_sections_and_points(self) -> ExamSpec:
        if self.sections and self.problems:
            raise ValueError("Specify either 'sections' or top-level 'problems', not both")
        if self.problems:
            self.sections = [SectionSpec(name="Problems", problems=self.problems)]
            self.problems = []
        if not self.sections:
            raise ValueError("ExamSpec must contain at least one section or problem")

        all_probs = self.all_problems()
        seen_ids: set[str] = set()
        for prob in all_probs:
            if prob.id in seen_ids:
                raise ValueError(f"Duplicate problem id '{prob.id}' in exam spec")
            seen_ids.add(prob.id)

        computed_total = sum(prob.points for prob in all_probs)
        if self.total_points is None:
            self.total_points = round(computed_total, 4)
        elif abs(self.total_points - computed_total) > 1e-4:
            raise ValueError(
                f"total_points ({self.total_points}) does not match sum of problem points "
                f"({computed_total})"
            )
        return self

    def all_problems(self) -> list[ProblemSpec]:
        """Return all problems flattened in document order."""
        return [prob for section in self.sections for prob in section.problems]


class AttemptProblemResult(BaseModel):
    """Per-problem review entry exported from the timed runner or logged manually."""

    problem_id: str = Field(min_length=1)
    status: AttemptStatus
    tags: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return [t.strip().lower() for t in values if t.strip()]


class AttemptRecord(BaseModel):
    """Timed simulation attempt telemetry record."""

    exam_title: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    duration_seconds_used: int = Field(default=0, ge=0)
    results: list[AttemptProblemResult] = Field(default_factory=list)


def format_points(value: float) -> str:
    """Format point totals cleanly without trailing '.0' for whole integers."""
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def load_exam_spec(path: str | Path) -> ExamSpec:
    """Load and validate an ExamSpec from a YAML file."""
    spec_path = Path(path)
    raw = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected YAML mapping in {spec_path}")
    return ExamSpec.model_validate(raw)


def save_exam_spec(spec: ExamSpec, path: str | Path) -> Path:
    """Serialize an ExamSpec cleanly to YAML."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = spec.model_dump(mode="json", exclude={"problems"})
    out_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return out_path
