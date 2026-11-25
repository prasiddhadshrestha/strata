"""Zero-TeX display math rendering via matplotlib.mathtext."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import mathtext  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from PIL import Image as PILImage  # noqa: E402

_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


def render_mathtext_png(
    expression: str,
    cache_dir: str | Path,
    dpi: int = 300,
    fontsize: int = 12,
) -> tuple[Path, float, float]:
    """Render a LaTeX-style math string to a PNG without a local TeX install.

    Returns (png_path, width_inches, height_inches).
    """
    cleaned = expression.strip()
    if not cleaned:
        raise ValueError("Cannot render empty math expression")

    if not (cleaned.startswith("$") and cleaned.endswith("$")):
        math_str = f"${cleaned}$"
    else:
        math_str = cleaned

    digest = hashlib.sha256(f"{math_str}|{dpi}|{fontsize}".encode()).hexdigest()[:16]
    out_dir = Path(cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"math_{digest}.png"

    if not out_path.exists():
        prop = FontProperties(family="serif", size=fontsize)
        mathtext.math_to_image(math_str, str(out_path), prop=prop, dpi=dpi, format="png")

    with PILImage.open(out_path) as img:
        px_w, px_h = img.size

    return out_path, px_w / float(dpi), px_h / float(dpi)


def split_prompt_and_display_math(prompt: str) -> tuple[str, list[str]]:
    """Extract optional $$...$$ display math blocks from a prompt string."""
    blocks: list[str] = []

    def _collect(match: re.Match[str]) -> str:
        blocks.append(match.group(1).strip())
        return ""

    stripped = _DISPLAY_MATH_RE.sub(_collect, prompt).strip()
    return stripped, blocks
