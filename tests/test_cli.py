"""End-to-end CLI tests via Typer CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from typer.testing import CliRunner

from strata.cli import app

runner = CliRunner()


def test_cli_version_and_validate() -> None:
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert "strata" in res.stdout

    val = runner.invoke(app, ["validate", "examples/systems_algorithms.yaml"])
    assert val.exit_code == 0
    assert "VALID:" in val.stdout


def test_cli_build_and_triage(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    res = runner.invoke(
        app,
        [
            "build",
            "examples/systems_algorithms.yaml",
            "--out",
            str(build_dir),
            "--solutions",
        ],
    )
    assert res.exit_code == 0
    assert (build_dir / "systems_algorithms.pdf").exists()
    assert (build_dir / "systems_algorithms_runner.html").exists()
    assert (build_dir / "systems_algorithms_solutions.pdf").exists()

    triage_dir = tmp_path / "triage"
    t_res = runner.invoke(
        app,
        [
            "triage",
            "--bank",
            "examples/calculus_optimization.yaml",
            "--bank",
            "examples/systems_algorithms.yaml",
            "--attempts",
            "examples/attempts",
            "--minutes",
            "15",
            "--out",
            str(triage_dir),
        ],
    )
    assert t_res.exit_code == 0
    assert (triage_dir / "triage_spec.pdf").exists()
    assert (triage_dir / "triage_spec_runner.html").exists()
    report = json.loads((triage_dir / "triage_report.json").read_text(encoding="utf-8"))
    assert "calc-03" in report["selected_problem_ids"]
    assert "boundary-condition" in report["mastered_tags"]


def test_cli_crop(tmp_path: Path) -> None:
    src = tmp_path / "raw.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    c.rect(100, 400, 200, 100, fill=1)
    c.showPage()
    c.save()

    out_png = tmp_path / "fig.png"
    res = runner.invoke(
        app,
        ["crop", str(src), "--page", "0", "--bbox", "0.1,0.2,0.7,0.7", "--out", str(out_png)],
    )
    assert res.exit_code == 0
    assert out_png.exists()
