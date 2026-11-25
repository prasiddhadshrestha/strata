"""Stateless topic-tag scoring and targeted micro-exam packing engine."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from strata.models import AttemptRecord, ExamSpec, ProblemSpec, SectionSpec, load_exam_spec


@dataclass
class TagMetrics:
    """Aggregated telemetry and priority weight for a single topic tag."""

    tag: str
    passes: int = 0
    partials: int = 0
    misses: int = 0
    pass_streak: int = 0
    last_attempt_idx: int = -1
    mastered: bool = False
    priority_score: float = 0.0


@dataclass
class TriageReport:
    """Structured diagnostic summary emitted alongside a generated triage exam."""

    total_bank_problems: int
    total_attempts_loaded: int
    active_tags: list[TagMetrics] = field(default_factory=list)
    mastered_tags: list[TagMetrics] = field(default_factory=list)
    selected_problem_ids: list[str] = field(default_factory=list)
    estimated_minutes: float = 0.0


def _resolve_problem_paths(prob: ProblemSpec, source_dir: Path) -> ProblemSpec:
    """Normalize relative image and crop source paths so saved triage specs stay portable."""
    updates: dict[str, object] = {}
    if prob.image:
        img_p = Path(prob.image)
        if not img_p.is_absolute():
            updates["image"] = str((source_dir / img_p).resolve())
    if prob.crop is not None:
        pdf_p = Path(prob.crop.source_pdf)
        if not pdf_p.is_absolute():
            updates["crop"] = prob.crop.model_copy(
                update={"source_pdf": str((source_dir / pdf_p).resolve())}
            )
    return prob.model_copy(update=updates) if updates else prob


def load_problem_banks(paths: list[Path]) -> list[ProblemSpec]:
    """Load unique problems from YAML specs or directories of YAML specs."""
    yaml_files: list[Path] = []
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(f"Bank path not found: {p}")
        if p.is_dir():
            yaml_files.extend(sorted(p.glob("*.yaml")))
            yaml_files.extend(sorted(p.glob("*.yml")))
        else:
            yaml_files.append(p)

    problems: list[ProblemSpec] = []
    seen_ids: set[str] = set()
    for yf in yaml_files:
        spec = load_exam_spec(yf)
        source_dir = yf.resolve().parent
        for prob in spec.all_problems():
            if prob.id not in seen_ids:
                seen_ids.add(prob.id)
                problems.append(_resolve_problem_paths(prob, source_dir))
    return problems


def load_attempts(paths: list[Path]) -> list[AttemptRecord]:
    """Load and chronologically sort AttemptRecord JSON telemetry files."""
    json_files: list[Path] = []
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(f"Attempt path not found: {p}")
        if p.is_dir():
            json_files.extend(sorted(p.glob("*.json")))
        else:
            json_files.append(p)

    records: list[AttemptRecord] = []
    for jf in json_files:
        raw = json.loads(jf.read_text(encoding="utf-8"))
        records.append(AttemptRecord.model_validate(raw))

    records.sort(key=lambda r: r.timestamp)
    return records


def score_tags(
    attempts: list[AttemptRecord],
    mastery_streak: int = 2,
) -> tuple[dict[str, TagMetrics], set[str], set[str]]:
    """Compute per-tag priority weights, mastery flags, and per-problem history sets."""
    tag_map: dict[str, TagMetrics] = {}
    passed_problems: set[str] = set()
    missed_problems: set[str] = set()

    total_attempts = max(1, len(attempts))
    for attempt_idx, attempt in enumerate(attempts):
        for res in attempt.results:
            if res.status == "pass":
                passed_problems.add(res.problem_id)
                missed_problems.discard(res.problem_id)
            else:
                missed_problems.add(res.problem_id)
                passed_problems.discard(res.problem_id)

            for tag in res.tags:
                metrics = tag_map.setdefault(tag, TagMetrics(tag=tag))
                metrics.last_attempt_idx = attempt_idx
                if res.status == "pass":
                    metrics.passes += 1
                    metrics.pass_streak += 1
                elif res.status == "partial":
                    metrics.partials += 1
                    metrics.pass_streak = 0
                else:
                    metrics.misses += 1
                    metrics.pass_streak = 0

    for metrics in tag_map.values():
        has_failures = (metrics.misses + metrics.partials) > 0
        metrics.mastered = has_failures and metrics.pass_streak >= mastery_streak
        if not has_failures or metrics.mastered:
            metrics.priority_score = 0.0
        else:
            recency_factor = 1.0 + 0.25 * (metrics.last_attempt_idx / float(total_attempts))
            base = metrics.misses * 3.0 + metrics.partials * 1.5
            metrics.priority_score = round(base * recency_factor, 4)

    return tag_map, passed_problems, missed_problems


def build_triage_exam(
    bank_problems: list[ProblemSpec],
    attempts: list[AttemptRecord],
    target_minutes: int = 15,
    mastery_streak: int = 2,
    title: str = "Targeted Review Session",
    explicit_tags: list[str] | None = None,
    include_mastered: bool = False,
) -> tuple[ExamSpec, TriageReport]:
    """Pack a targeted micro-exam from bank problems matching active weak tags."""
    if not bank_problems:
        raise ValueError("Problem bank is empty; cannot construct triage exam")

    tag_map, passed_problems, missed_problems = score_tags(
        attempts, mastery_streak=mastery_streak
    )

    if explicit_tags:
        wanted = {t.strip().lower() for t in explicit_tags if t.strip()}
        for tag in wanted:
            if tag not in tag_map:
                tag_map[tag] = TagMetrics(tag=tag, misses=1, priority_score=3.0)
            elif tag_map[tag].priority_score == 0.0:
                tag_map[tag].priority_score = 2.0

    active_metrics = sorted(
        (m for m in tag_map.values() if m.priority_score > 0.0),
        key=lambda m: (-m.priority_score, m.tag),
    )
    mastered_metrics = sorted(
        (m for m in tag_map.values() if m.mastered),
        key=lambda m: m.tag,
    )

    active_weights = {m.tag: m.priority_score for m in active_metrics}
    if include_mastered and not active_weights:
        active_weights = {m.tag: 1.0 for m in tag_map.values()}

    if not active_weights:
        raise ValueError(
            "No active unmastered tags found in attempts. "
            "Pass --tags or --include-mastered to build a drill."
        )

    scored_candidates: list[tuple[float, ProblemSpec]] = []
    for prob in bank_problems:
        if explicit_tags and not (set(prob.tags) & {t.lower() for t in explicit_tags}):
            continue
        tag_score = sum(active_weights.get(t, 0.0) for t in prob.tags)
        if tag_score <= 0.0:
            continue

        novelty_bonus = 0.0
        if prob.id in missed_problems:
            novelty_bonus = 1.5
        elif prob.id not in passed_problems:
            novelty_bonus = 2.0

        scored_candidates.append((tag_score + novelty_bonus, prob))

    if not scored_candidates:
        raise ValueError("No problems in bank match the active tags")

    scored_candidates.sort(key=lambda item: (-item[0], item[1].estimated_minutes, item[1].id))

    selected: list[ProblemSpec] = []
    used_minutes = 0.0
    for _score, prob in scored_candidates:
        if not selected or (used_minutes + prob.estimated_minutes <= float(target_minutes)):
            selected.append(prob)
            used_minutes += prob.estimated_minutes

    active_tag_names = ", ".join(m.tag for m in active_metrics[:5]) or ", ".join(active_weights)
    spec = ExamSpec(
        title=title,
        subtitle=f"Targeted topics: {active_tag_names}",
        duration_minutes=max(5, min(360, int(round(used_minutes)) or target_minutes)),
        layout="single",
        rules=[
            "Focused review set generated from recent attempt history.",
            "Show all intermediate steps clearly.",
        ],
        sections=[
            SectionSpec(
                name="Targeted Review",
                description=(
                    f"Packed to ~{used_minutes:.1f} min from {len(bank_problems)} bank items."
                ),
                problems=selected,
            )
        ],
    )

    report = TriageReport(
        total_bank_problems=len(bank_problems),
        total_attempts_loaded=len(attempts),
        active_tags=active_metrics,
        mastered_tags=mastered_metrics,
        selected_problem_ids=[p.id for p in selected],
        estimated_minutes=round(used_minutes, 2),
    )
    return spec, report
