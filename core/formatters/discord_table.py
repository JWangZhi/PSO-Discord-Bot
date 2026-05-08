"""
Discord ASCII Table Renderer
Converts structured JSON payloads into Discord-friendly ASCII tables, KV pairs, or bullet lists.
LLM decides structure + content → Python controls presentation.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ── Hard limits (Discord-friendly) ──────────────────────────────────────────
MAX_COLS = 5
MAX_ROWS = 8
MAX_CELL_LEN = 24
MAX_KV_VALUE_LEN = 60
MAX_BULLET_LEN = 120

# ── Helpers ─────────────────────────────────────────────────────────────────

def _trunc(text: Any, max_len: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _pad(text: str, width: int, align: str = "left") -> str:
    return text.rjust(width) if align == "right" else text.ljust(width)


def _looks_numeric(text: str) -> bool:
    return bool(re.match(r"^-?[\d,.]+%?$", text.strip()))


def _col_widths(headers: list[str], rows: list[list[str]]) -> list[int]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    return [min(w, MAX_CELL_LEN) for w in widths]


# ── Renderers ───────────────────────────────────────────────────────────────

def render_table(
    title: str,
    headers: list[str],
    rows: list[list[Any]],
    takeaway: str = "",
    cautions: list[str] | None = None,
) -> str:
    cautions = cautions or []
    headers = [_trunc(h, MAX_CELL_LEN) for h in headers[:MAX_COLS]]
    rows = [
        [_trunc(c, MAX_CELL_LEN) for c in row[:MAX_COLS]]
        for row in rows[:MAX_ROWS]
    ]

    widths = _col_widths(headers, rows)

    def _fmt(row: list[str]) -> str:
        cells = []
        for i, cell in enumerate(row):
            w = widths[i] if i < len(widths) else len(cell)
            align = "right" if _looks_numeric(cell) else "left"
            cells.append(_pad(_trunc(cell, w), w, align))
        return " | ".join(cells)

    sep = "-+-".join("-" * w for w in widths)
    lines: list[str] = []
    if title:
        lines.append(title)
    lines.append(_fmt(headers))
    lines.append(sep)
    for row in rows:
        lines.append(_fmt(row))

    if takeaway:
        lines.append("")
        lines.append(f">> {takeaway}")
    for c in cautions:
        lines.append(f" ! {_trunc(c, 120)}")

    return "```\n" + "\n".join(lines) + "\n```"


def render_kv(
    title: str,
    rows: list[dict[str, Any]],
    takeaway: str = "",
    cautions: list[str] | None = None,
) -> str:
    cautions = cautions or []
    labels = [_trunc(r.get("label", ""), MAX_CELL_LEN) for r in rows[:MAX_ROWS]]
    values = [_trunc(r.get("value", ""), MAX_KV_VALUE_LEN) for r in rows[:MAX_ROWS]]
    lw = min(max((len(l) for l in labels), default=5), MAX_CELL_LEN)

    lines: list[str] = []
    if title:
        lines.append(title)
    for label, value in zip(labels, values):
        lines.append(f"{_pad(label, lw)} | {value}")

    if takeaway:
        lines.append("")
        lines.append(f">> {takeaway}")
    for c in cautions:
        lines.append(f" ! {_trunc(c, 120)}")

    return "```\n" + "\n".join(lines) + "\n```"


def render_bullets(
    title: str,
    bullets: list[str],
    takeaway: str = "",
    cautions: list[str] | None = None,
) -> str:
    cautions = cautions or []
    lines: list[str] = []
    if title:
        lines.append(title)
    for b in bullets[:12]:
        lines.append(f"• {_trunc(b, MAX_BULLET_LEN)}")

    if takeaway:
        lines.append("")
        lines.append(f">> {takeaway}")
    for c in cautions:
        lines.append(f" ! {_trunc(c, 120)}")

    return "```\n" + "\n".join(lines) + "\n```"


# ── Public API ──────────────────────────────────────────────────────────────

def render_response(payload: dict[str, Any]) -> str:
    """Render a validated structured payload into a Discord ASCII block."""
    fmt = payload.get("format", "bullets")
    title = payload.get("title", "")

    if fmt == "table":
        return render_table(
            title=title,
            headers=payload.get("headers", []),
            rows=payload.get("rows", []),
            takeaway=payload.get("takeaway", ""),
            cautions=payload.get("cautions", []),
        )
    if fmt == "kv":
        return render_kv(
            title=title,
            rows=payload.get("rows", []),
            takeaway=payload.get("takeaway", ""),
            cautions=payload.get("cautions", []),
        )
    return render_bullets(
        title=title,
        bullets=payload.get("bullets", []),
        takeaway=payload.get("takeaway", ""),
        cautions=payload.get("cautions", []),
    )


def validate_payload(payload: dict[str, Any]) -> bool:
    """Check that the payload has the minimum required fields for its format."""
    fmt = payload.get("format")
    if fmt not in ("table", "kv", "bullets"):
        return False
    if fmt == "table":
        return bool(payload.get("headers")) and isinstance(payload.get("rows"), list)
    if fmt == "kv":
        return isinstance(payload.get("rows"), list) and len(payload["rows"]) > 0
    # bullets
    return isinstance(payload.get("bullets"), list) and len(payload["bullets"]) > 0


def try_parse_structured(text: str) -> dict[str, Any] | None:
    """Attempt to extract a structured JSON payload from LLM output.

    Returns the parsed dict if valid, or None if the text is not structured JSON.
    """
    clean = text.strip()
    # Strip markdown code fences if present
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        data = json.loads(clean)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if not validate_payload(data):
        return None
    return data
