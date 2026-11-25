"""PyMuPDF bounding-box diagram extraction utilities."""

from __future__ import annotations

from pathlib import Path

import pymupdf


def crop_pdf_region(
    pdf_path: str | Path,
    page_num: int,
    bbox_ratios: tuple[float, float, float, float],
    output_png_path: str | Path,
    dpi: int = 200,
) -> Path:
    """Crop a normalized [x0, y0, x1, y1] region from a PDF page into a PNG file."""
    src = Path(pdf_path)
    if not src.exists():
        raise FileNotFoundError(f"Source PDF not found: {src}")

    x0, y0, x1, y1 = bbox_ratios
    if not all(0.0 <= c <= 1.0 for c in bbox_ratios) or x0 >= x1 or y0 >= y1:
        raise ValueError(f"Invalid normalized bbox ratios: {bbox_ratios}")

    out = Path(output_png_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with pymupdf.open(src) as doc:
        if page_num < 0 or page_num >= len(doc):
            raise IndexError(f"Page {page_num} out of range (0..{len(doc) - 1}) in {src}")
        page = doc[page_num]
        rect = page.rect
        clip_rect = pymupdf.Rect(
            rect.width * x0,
            rect.height * y0,
            rect.width * x1,
            rect.height * y1,
        )
        pix = page.get_pixmap(dpi=dpi, clip=clip_rect)
        pix.save(str(out))

    return out
