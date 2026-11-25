"""Air-gapped single-file HTML timed runner with Web Audio chimes and telemetry export."""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

import pymupdf

from strata.models import ExamSpec, format_points
from strata.pdf_builder import PdfBuildResult


def _rasterize_pdf_pages_b64(
    pdf_path: Path, exam_pages: int, dpi: int = 160
) -> tuple[list[str], list[str]]:
    """Render PDF pages to Base64 PNG strings split into (exam_pages, key_pages)."""
    exam_b64: list[str] = []
    key_b64: list[str] = []

    with pymupdf.open(pdf_path) as doc:
        for idx in range(len(doc)):
            pix = doc[idx].get_pixmap(dpi=dpi)
            encoded = base64.b64encode(pix.tobytes("png")).decode("ascii")
            if idx < exam_pages:
                exam_b64.append(encoded)
            else:
                key_b64.append(encoded)

    return exam_b64, key_b64


def build_runner_html(
    spec: ExamSpec,
    pdf_result: PdfBuildResult,
    output_html: str | Path,
    dpi: int = 160,
) -> Path:
    """Compile a self-contained offline HTML runner with wall-clock timer and JSON export."""
    out_path = Path(output_html)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    exam_b64, key_b64 = _rasterize_pdf_pages_b64(
        pdf_result.pdf_path, exam_pages=pdf_result.exam_pages, dpi=dpi
    )

    exam_pages_html = "\n".join(
        f"""
        <div class="paper-sheet">
          <div class="page-label">Exam Page {i} of {len(exam_b64)}</div>
          <img src="data:image/png;base64,{b64}" alt="Exam Page {i}" />
        </div>"""
        for i, b64 in enumerate(exam_b64, start=1)
    )

    key_pages_html = "\n".join(
        f"""
        <div class="paper-sheet key-sheet">
          <div class="page-label key-label">Answer Key Page {i} of {len(key_b64)}</div>
          <img src="data:image/png;base64,{b64}" alt="Answer Key Page {i}" />
        </div>"""
        for i, b64 in enumerate(key_b64, start=1)
    )

    problems_payload = [
        {
            "id": p.id,
            "num": idx,
            "title": p.title or p.prompt[:64],
            "points": p.points,
            "tags": p.tags,
            "answer": p.answer,
            "explanation": p.explanation,
        }
        for idx, p in enumerate(spec.all_problems(), start=1)
    ]

    safe_json_problems = json.dumps(problems_payload).replace("</", "<\\/")
    safe_json_title = json.dumps(spec.title).replace("</", "<\\/")
    safe_title = html.escape(spec.title)
    total_pts = format_points(spec.total_points or 0.0)
    has_key_pages = len(key_b64) > 0
    has_inline_solutions = any(p.answer or p.explanation for p in spec.all_problems())
    guide_list = spec.guidelines or [
        "Keep external tabs and reference docs closed while the timer is running.",
        "Scan all problems during the first 60 seconds and budget time.",
        "When finished, open Review & Key to grade items and export attempt.json.",
    ]
    guides_html = "".join(f"<li>{html.escape(item)}</li>" for item in guide_list)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{safe_title} | Strata Runner</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg: #0a0a0a;
      --surface: #12151c;
      --surface-alt: #1c222d;
      --border: #262e3d;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --warn: #f59e0b;
      --danger: #ef4444;
      --ok: #22c55e;
    }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding-top: 78px;
      padding-bottom: 56px;
    }}
    .top-bar {{
      position: fixed;
      top: 0; left: 0; right: 0;
      min-height: 64px;
      background: rgba(10, 10, 10, 0.96);
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 20px;
      z-index: 1000;
    }}
    .title-group {{ display: flex; align-items: center; gap: 10px; }}
    .badge {{
      background: #0284c7;
      color: #fff;
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .title-text {{ font-weight: 700; font-size: 14px; color: var(--text); }}
    .timer-group {{ display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }}
    .timer-box {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 22px;
      font-weight: 800;
      color: var(--accent);
      background: var(--surface);
      padding: 4px 12px;
      border-radius: 6px;
      border: 1px solid var(--border);
      min-width: 92px;
      text-align: center;
    }}
    .timer-box.warning {{ color: var(--warn); border-color: var(--warn); }}
    .timer-box.danger {{
      color: var(--danger);
      border-color: var(--danger);
      animation: pulse 1s infinite;
    }}
    @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.6; }} }}
    button {{
      background: #2563eb;
      color: #fff;
      border: 1px solid transparent;
      padding: 7px 13px;
      border-radius: 6px;
      font-weight: 600;
      font-size: 12.5px;
      cursor: pointer;
    }}
    button:hover:not(:disabled) {{ background: #1d4ed8; }}
    button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    button.sec {{ background: var(--surface-alt); color: #cbd5e1; border-color: var(--border); }}
    button.sec:hover:not(:disabled) {{ background: #293548; }}
    button.ok {{ background: #16a34a; }}
    button.ok:hover:not(:disabled) {{ background: #15803d; }}
    .progress-track {{
      position: fixed;
      top: 64px; left: 0; right: 0;
      height: 3px;
      background: var(--surface-alt);
      z-index: 999;
    }}
    .progress-fill {{
      height: 100%;
      width: 100%;
      background: var(--accent);
      transition: width 0.25s linear;
    }}
    .banner {{
      max-width: 860px;
      margin: 0 auto 16px;
      padding: 12px 16px;
      border-radius: 8px;
      background: var(--surface);
      border: 1px solid var(--border);
      font-size: 13px;
      color: var(--muted);
    }}
    .banner ul {{ margin-left: 18px; margin-top: 6px; line-height: 1.5; }}
    .timeup-banner {{
      display: none;
      max-width: 860px;
      margin: 0 auto 16px;
      padding: 12px 16px;
      border-radius: 8px;
      background: rgba(239, 68, 68, 0.14);
      border: 1px solid var(--danger);
      color: #fecaca;
      font-weight: 700;
      font-size: 14px;
    }}
    .container {{
      max-width: 860px;
      margin: 0 auto;
      padding: 0 16px;
      display: flex;
      flex-direction: column;
      gap: 22px;
    }}
    .paper-sheet {{
      background: #fff;
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45);
    }}
    .page-label {{
      background: #e2e8f0;
      color: #334155;
      font-size: 11.5px;
      font-weight: 700;
      padding: 6px 14px;
      border-bottom: 1px solid #cbd5e1;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .key-label {{ background: #fef3c7; color: #92400e; border-bottom-color: #fde68a; }}
    .paper-sheet img {{ width: 100%; height: auto; display: block; }}
    .key-container {{ display: none; margin-top: 8px; }}
    .key-container.unlocked {{ display: flex; flex-direction: column; gap: 22px; }}
    .drawer {{
      position: fixed;
      top: 64px; right: 0; bottom: 0;
      width: min(440px, 100vw);
      background: var(--surface);
      border-left: 1px solid var(--border);
      transform: translateX(100%);
      transition: transform 0.2s ease;
      z-index: 1050;
      display: flex;
      flex-direction: column;
    }}
    .drawer.open {{ transform: translateX(0); }}
    .drawer-head {{
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .drawer-body {{
      flex: 1;
      overflow-y: auto;
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    .review-card {{
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 12.5px;
    }}
    .review-card-head {{
      display: flex;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
      font-weight: 700;
    }}
    .tag-row {{ display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }}
    .tag-pill {{
      font-family: ui-monospace, monospace;
      font-size: 11px;
      background: var(--surface-alt);
      color: var(--accent);
      padding: 2px 6px;
      border-radius: 4px;
    }}
    .status-btns {{ display: flex; gap: 6px; margin-bottom: 8px; }}
    .status-btn {{
      flex: 1;
      background: var(--surface-alt);
      color: var(--muted);
      padding: 5px 8px;
      font-size: 12px;
    }}
    .status-btn.active-pass {{ background: #166534; color: #dcfce7; border-color: #22c55e; }}
    .status-btn.active-partial {{ background: #854d0e; color: #fef9c3; border-color: #eab308; }}
    .status-btn.active-missed {{ background: #991b1b; color: #fee2e2; border-color: #ef4444; }}
    .note-input {{
      width: 100%;
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 5px;
      padding: 6px 8px;
      font-size: 12px;
    }}
    .answer-reveal {{
      display: none;
      margin-top: 6px;
      color: #cbd5e1;
      font-size: 11.5px;
      line-height: 1.4;
    }}
    .answer-reveal.visible {{ display: block; }}
    .drawer-foot {{
      padding: 12px 16px;
      border-top: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .summary-line {{ font-size: 12.5px; color: var(--muted); }}
    .export-notice {{ font-size: 12px; color: var(--warn); min-height: 16px; }}
  </style>
</head>
<body>
  <div class="top-bar">
    <div class="title-group">
      <span class="badge">Strata</span>
      <span class="title-text">{safe_title} ({total_pts} pts)</span>
    </div>
    <div class="timer-group">
      <div class="timer-box" id="timer">{spec.duration_minutes:02d}:00</div>
      <button id="startBtn">Start</button>
      <button class="sec" id="resetBtn">Reset</button>
      <button class="sec" id="muteBtn">Sound: ON</button>
      <button class="sec" id="reviewBtn">Review &amp; Key</button>
    </div>
  </div>
  <div class="progress-track"><div class="progress-fill" id="progress"></div></div>

  <div class="timeup-banner" id="timeupBanner">
    Time expired. Open Review &amp; Key to grade items and export attempt.json.
  </div>

  <div class="banner">
    <strong>Exam Guidelines</strong>
    <ul>{guides_html}</ul>
  </div>

  <div class="container">
    {exam_pages_html}
    <div class="key-container" id="keyContainer">
      {key_pages_html}
    </div>
  </div>

  <aside class="drawer" id="reviewDrawer">
    <div class="drawer-head">
      <strong>Post-Run Review &amp; Export</strong>
      <button class="sec" id="closeDrawerBtn">Close</button>
    </div>
    <div class="drawer-body" id="reviewList"></div>
    <div class="drawer-foot">
      <div class="summary-line" id="reviewSummary">
        Select Pass, Partial, or Missed for each problem to log telemetry.
      </div>
      <div class="export-notice" id="exportNotice"></div>
      <div style="display:flex; gap:8px;">
        <button class="sec" id="revealKeyBtn" style="flex:1;">Reveal Key &amp; Solutions</button>
        <button class="ok" id="exportBtn" style="flex:1;">Export attempt.json</button>
      </div>
    </div>
  </aside>

  <script>
    const EXAM_TITLE = {safe_json_title};
    const PROBLEMS = {safe_json_problems};
    const INITIAL_SEC = {spec.duration_minutes * 60};
    const HAS_KEY_PAGES = {str(has_key_pages).lower()};
    const HAS_INLINE_SOLUTIONS = {str(has_inline_solutions).lower()};

    let sec = INITIAL_SEC;
    let deadlineMs = null;
    let iv = null;
    let running = false;
    let muted = false;
    let keyRevealed = false;
    let warn5Fired = false;
    let warn1Fired = false;

    const state = {{}};
    PROBLEMS.forEach((p) => {{
      state[p.id] = {{ status: "ungraded", notes: "" }};
    }});

    const tEl = document.getElementById("timer");
    const pEl = document.getElementById("progress");
    const sBtn = document.getElementById("startBtn");
    const mBtn = document.getElementById("muteBtn");
    const drawer = document.getElementById("reviewDrawer");
    const keyContainer = document.getElementById("keyContainer");
    const revealKeyBtn = document.getElementById("revealKeyBtn");
    const timeupBanner = document.getElementById("timeupBanner");
    const exportNotice = document.getElementById("exportNotice");

    if (!HAS_KEY_PAGES && !HAS_INLINE_SOLUTIONS) {{
      revealKeyBtn.style.display = "none";
    }} else if (!HAS_KEY_PAGES) {{
      revealKeyBtn.textContent = "Reveal Solutions";
    }}

    function playChime(freqs, durSec) {{
      if (muted) return;
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      try {{
        const ctx = new AudioCtx();
        freqs.forEach((freq, idx) => {{
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = "sine";
          osc.frequency.value = freq;
          const startAt = ctx.currentTime + idx * (durSec + 0.06);
          gain.gain.setValueAtTime(0.12, startAt);
          gain.gain.exponentialRampToValueAtTime(0.001, startAt + durSec);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(startAt);
          osc.stop(startAt + durSec);
        }});
      }} catch (_err) {{}}
    }}

    function renderTimer() {{
      const m = Math.floor(sec / 60).toString().padStart(2, "0");
      const s = (sec % 60).toString().padStart(2, "0");
      tEl.textContent = m + ":" + s;
      pEl.style.width = Math.max(0, (sec / INITIAL_SEC) * 100) + "%";
      tEl.className = "timer-box";
      if (sec <= 300 && sec > 60) tEl.classList.add("warning");
      if (sec <= 60) tEl.classList.add("danger");
    }}

    function tickTimer() {{
      const remaining = Math.max(0, Math.ceil((deadlineMs - Date.now()) / 1000));
      if (remaining === sec) return;
      sec = remaining;
      renderTimer();
      if (sec <= 300 && sec > 60 && !warn5Fired) {{
        warn5Fired = true;
        playChime([523.25, 659.25], 0.18);
      }}
      if (sec <= 60 && sec > 0 && !warn1Fired) {{
        warn1Fired = true;
        playChime([783.99, 783.99], 0.16);
      }}
      if (sec === 0) {{
        clearInterval(iv);
        iv = null;
        running = false;
        deadlineMs = null;
        sBtn.textContent = "Finished";
        sBtn.disabled = true;
        timeupBanner.style.display = "block";
        playChime([523.25, 659.25, 783.99], 0.22);
      }}
    }}

    function toggleTimer() {{
      if (sec <= 0) return;
      if (running) {{
        clearInterval(iv);
        iv = null;
        running = false;
        deadlineMs = null;
        sBtn.textContent = "Resume";
        return;
      }}
      running = true;
      sBtn.textContent = "Pause";
      deadlineMs = Date.now() + sec * 1000;
      iv = setInterval(tickTimer, 250);
    }}

    function resetTimer() {{
      clearInterval(iv);
      iv = null;
      running = false;
      deadlineMs = null;
      sec = INITIAL_SEC;
      warn5Fired = false;
      warn1Fired = false;
      sBtn.textContent = "Start";
      sBtn.disabled = false;
      timeupBanner.style.display = "none";
      renderTimer();
    }}

    function updateSummary() {{
      let earned = 0;
      let total = 0;
      let gradedCount = 0;
      const missedTags = new Set();
      PROBLEMS.forEach((p) => {{
        total += p.points;
        const st = state[p.id].status;
        if (st !== "ungraded") gradedCount++;
        if (st === "pass") earned += p.points;
        else if (st === "partial") earned += p.points * 0.5;
        if (st === "missed" || st === "partial") {{
          p.tags.forEach((t) => missedTags.add(t));
        }}
      }});
      const tagsTxt = missedTags.size ? Array.from(missedTags).join(", ") : "none";
      document.getElementById("reviewSummary").textContent =
        "Graded: " + gradedCount + "/" + PROBLEMS.length +
        " | Score: " + earned + "/" + total + " pts | Weak tags: " + tagsTxt;
    }}

    function renderReviewCards() {{
      const list = document.getElementById("reviewList");
      list.innerHTML = "";
      PROBLEMS.forEach((p) => {{
        const card = document.createElement("div");
        card.className = "review-card";

        const head = document.createElement("div");
        head.className = "review-card-head";
        head.textContent = "(" + p.num + ") " + p.id + " [" + p.points + " pts]";
        card.appendChild(head);

        if (p.tags.length) {{
          const tagRow = document.createElement("div");
          tagRow.className = "tag-row";
          p.tags.forEach((t) => {{
            const pill = document.createElement("span");
            pill.className = "tag-pill";
            pill.textContent = t;
            tagRow.appendChild(pill);
          }});
          card.appendChild(tagRow);
        }}

        const btnRow = document.createElement("div");
        btnRow.className = "status-btns";
        ["pass", "partial", "missed"].forEach((st) => {{
          const btn = document.createElement("button");
          btn.className = "status-btn";
          btn.textContent = st.toUpperCase();
          if (state[p.id].status === st) btn.classList.add("active-" + st);
          btn.addEventListener("click", () => {{
            state[p.id].status = st;
            exportNotice.textContent = "";
            renderReviewCards();
            updateSummary();
          }});
          btnRow.appendChild(btn);
        }});
        card.appendChild(btnRow);

        const note = document.createElement("input");
        note.className = "note-input";
        note.placeholder = "Optional review note...";
        note.value = state[p.id].notes;
        note.addEventListener("input", (e) => {{
          state[p.id].notes = e.target.value;
        }});
        card.appendChild(note);

        if (p.answer || p.explanation) {{
          const ans = document.createElement("div");
          ans.className = "answer-reveal" + (keyRevealed ? " visible" : "");
          const body = p.answer + (p.explanation ? " | " + p.explanation : "");
          ans.textContent = "Key: " + body;
          card.appendChild(ans);
        }}

        list.appendChild(card);
      }});
      updateSummary();
    }}

    function exportAttempt() {{
      const gradedResults = PROBLEMS
        .filter((p) => state[p.id].status !== "ungraded")
        .map((p) => ({{
          problem_id: p.id,
          status: state[p.id].status,
          tags: p.tags,
          notes: state[p.id].notes
        }}));

      if (gradedResults.length === 0) {{
        exportNotice.textContent = "Grade at least one problem before exporting attempt.json.";
        return;
      }}

      const skipped = PROBLEMS.length - gradedResults.length;
      exportNotice.textContent = skipped > 0
        ? "Exported " + gradedResults.length + " graded items (" + skipped + " ungraded omitted)."
        : "Exported all " + gradedResults.length + " graded items.";

      const payload = {{
        exam_title: EXAM_TITLE,
        timestamp: new Date().toISOString(),
        duration_seconds_used: Math.max(0, INITIAL_SEC - sec),
        results: gradedResults
      }};
      const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: "application/json" }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "attempt_" + new Date().toISOString().replace(/[:.]/g, "-") + ".json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }}

    sBtn.addEventListener("click", toggleTimer);
    document.getElementById("resetBtn").addEventListener("click", resetTimer);
    mBtn.addEventListener("click", () => {{
      muted = !muted;
      mBtn.textContent = muted ? "Sound: OFF" : "Sound: ON";
    }});
    document.getElementById("reviewBtn").addEventListener("click", () => {{
      drawer.classList.toggle("open");
    }});
    document.getElementById("closeDrawerBtn").addEventListener("click", () => {{
      drawer.classList.remove("open");
    }});
    revealKeyBtn.addEventListener("click", () => {{
      keyRevealed = true;
      renderReviewCards();
      if (HAS_KEY_PAGES) {{
        keyContainer.classList.add("unlocked");
        keyContainer.scrollIntoView({{ behavior: "smooth" }});
      }}
      revealKeyBtn.disabled = true;
      revealKeyBtn.textContent = "Key Revealed";
    }});
    document.getElementById("exportBtn").addEventListener("click", exportAttempt);

    renderTimer();
    renderReviewCards();
  </script>
</body>
</html>"""

    out_path.write_text(html_doc, encoding="utf-8")
    return out_path
