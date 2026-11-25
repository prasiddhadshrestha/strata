"""Tests for PDF bounding-box cropping and zero-TeX mathtext rendering."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from strata.crop import crop_pdf_region
from strata.math_render import render_mathtext_png, split_prompt_and_display_math


def _make_sample_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFillColorRGB(0.1, 0.4, 0.8)
    c.rect(100, 400, 200, 150, fill=1)
    c.showPage()
    c.save()
    return path


def test_crop_pdf_region_creates_png(tmp_path: Path) -> None:
    pdf = _make_sample_pdf(tmp_path / "source.pdf")
    out_png = tmp_path / "cropped.png"
    res = crop_pdf_region(pdf, 0, (0.1, 0.2, 0.6, 0.6), out_png, dpi=150)
    assert res.exists()
    with Image.open(res) as img:
        assert img.width > 100
        assert img.height > 100


def test_crop_pdf_region_errors(tmp_path: Path) -> None:
    pdf = _make_sample_pdf(tmp_path / "source.pdf")
    with pytest.raises(FileNotFoundError):
        crop_pdf_region(tmp_path / "missing.pdf", 0, (0.1, 0.1, 0.5, 0.5), tmp_path / "x.png")
    with pytest.raises(IndexError):
        crop_pdf_region(pdf, 5, (0.1, 0.1, 0.5, 0.5), tmp_path / "x.png")


def test_render_mathtext_png_and_cache(tmp_path: Path) -> None:
    p1, w1, h1 = render_mathtext_png(r"\lim_{x \to 0} \frac{\sin x}{x} = 1", tmp_path, dpi=200)
    p2, w2, h2 = render_mathtext_png(r"\lim_{x \to 0} \frac{\sin x}{x} = 1", tmp_path, dpi=200)
    assert p1 == p2
    assert p1.exists()
    assert w1 == w2 and h1 == h2
    assert w1 > 0.2 and h1 > 0.1


def test_split_prompt_and_display_math() -> None:
    text, blocks = split_prompt_and_display_math("Compute:\n$$x^2 + 1$$\nNow simplify.")
    assert blocks == ["x^2 + 1"]
    assert "$$" not in text
    assert "Compute:" in text
