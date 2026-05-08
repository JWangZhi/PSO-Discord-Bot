Được, mình sẽ chốt theo hướng **flow hybrid JSON → ASCII renderer** vì nó ổn định hơn để bot tự format bảng trên Discord. Cách này hợp với bot PSO2NGS của bạn vì LLM chỉ lo reasoning, còn Python lo căn bảng, nên dễ debug hơn nhiều. [github](https://github.com/johnyob/Ascii-Table)

## Kiến trúc

Flow mình đề xuất là: **LLM trả JSON có cấu trúc → Python validate → Python render ASCII table → bot gửi Discord**. Như vậy bạn tránh được chuyện model tự căn spacing sai hoặc trả markdown table không render đúng trong Discord. [stackoverflow](https://stackoverflow.com/questions/73573968/how-can-i-beautify-json-output-in-a-discord-py-bot-message)

Mình khuyên JSON output nên có 3 lớp:
- `format`: `"table"`, `"kv"`, hoặc `"bullets"`.
- `headers` và `rows` nếu là table.
- `takeaway` và `cautions` cho phần kết luận ngắn. [github](https://github.com/AllMightySauron/ascii-table3)

## Prompt cho LLM

Bạn có thể dùng prompt system/dev kiểu này:

```text
You are a formatting and reasoning assistant for a Discord bot.

Your task:
1) Analyze the user's question.
2) Decide whether the best response format is:
   - "table" for comparisons or ranked options
   - "kv" for one-state summary or profile
   - "bullets" for explanations or advice
3) Return ONLY valid JSON.

Formatting rules:
- If you choose "table", provide:
  - format: "table"
  - title: short string
  - headers: array of short strings
  - rows: array of arrays of strings
  - takeaway: short string
  - cautions: array of short strings
- If you choose "kv", provide:
  - format: "kv"
  - title: short string
  - rows: array of objects with keys "label" and "value"
  - takeaway: short string
  - cautions: array of short strings
- If you choose "bullets", provide:
  - format: "bullets"
  - title: short string
  - bullets: array of short strings
  - takeaway: short string
  - cautions: array of short strings

Rules:
- Keep text concise and Discord-friendly.
- Never output Markdown tables.
- Never wrap the JSON in code fences.
- Never add extra commentary outside JSON.
- Keep headers and labels short.
- If a cell is too long, summarize it.
- Prefer numbers, short labels, and practical language.
- If data is uncertain, reflect that in takeaway or cautions.

Example table JSON:
{
  "format": "table",
  "title": "Best build options",
  "headers": ["Build", "Score", "Cost", "Note"],
  "rows": [
    ["Slayer A", "8.7", "Low", "Best balance"],
    ["Force C", "8.1", "High", "Highest burst"]
  ],
  "takeaway": "Slayer A is the safest recommendation.",
  "cautions": ["Force C costs more meseta."]
}
```

## Prompt riêng cho PSO2NGS

Nếu muốn sát game hơn, dùng bản này:

```text
You are a PSO2NGS Discord bot formatter.

Your job is to turn strategy answers into a compact JSON response that the bot can render as ASCII tables or short bullet lists.

Use "table" when comparing builds, weapons, augments, classes, or farming options.
Use "kv" when summarizing one player state, one loadout, or one recommendation.
Use "bullets" when the answer is mostly explanation, warnings, or advice.

Important:
- Output only valid JSON.
- Keep it short and readable on Discord.
- Prefer practical recommendations over theory.
- If multiple options are close, mention that in takeaway.
- If the user asks for quick understanding, favor table or kv format.
- Avoid long lore or long explanations in the main answer.
```

## Python renderer

Đây là renderer đơn giản, không phụ thuộc thư viện ngoài. Nó nhận JSON dict rồi trả về string ASCII phù hợp cho Discord.

```python
import json
from typing import Any, Dict, List

def truncate(text: Any, max_len: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= max_len else s[:max_len - 3] + "..."

def pad(text: str, width: int, align: str = "left") -> str:
    if align == "right":
        return text.rjust(width)
    return text.ljust(width)

def calc_widths(headers: List[str], rows: List[List[str]], max_widths: List[int] = None) -> List[int]:
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    if max_widths:
        widths = [min(w, max_widths[i]) for i, w in enumerate(widths)]
    return widths

def render_table(title: str, headers: List[str], rows: List[List[Any]], takeaway: str = "", cautions: List[str] = None) -> str:
    cautions = cautions or []
    headers = [str(h) for h in headers]
    rows = [[str(c) for c in row] for row in rows]

    max_widths = [24] * len(headers)
    widths = calc_widths(headers, rows, max_widths)

    def fmt_row(row: List[str]) -> str:
        cells = []
        for i, cell in enumerate(row):
            cell = truncate(cell, widths[i])
            align = "right" if cell.replace(".", "", 1).isdigit() else "left"
            cells.append(pad(cell, widths[i], align=align))
        return " | ".join(cells)

    sep = "-+-".join("-" * w for w in widths)

    out = []
    if title:
        out.append(title)
    out.append(fmt_row(headers))
    out.append(sep)
    for row in rows:
        padded = [truncate(cell, widths[i]) for i, cell in enumerate(row)]
        out.append(fmt_row(padded))

    if takeaway:
        out.append("")
        out.append(f"Takeaway: {takeaway}")

    for c in cautions:
        out.append(f"- {c}")

    return "```txt\n" + "\n".join(out) + "\n```"

def render_kv(title: str, rows: List[Dict[str, Any]], takeaway: str = "", cautions: List[str] = None) -> str:
    cautions = cautions or []
    labels = [truncate(r.get("label", ""), 24) for r in rows]
    values = [truncate(r.get("value", ""), 60) for r in rows]
    label_width = min(max(len(x) for x in labels) if labels else 5, 24)

    out = []
    if title:
        out.append(title)
    for label, value in zip(labels, values):
        out.append(f"{pad(label, label_width)} | {value}")

    if takeaway:
        out.append("")
        out.append(f"Takeaway: {takeaway}")

    for c in cautions:
        out.append(f"- {c}")

    return "```txt\n" + "\n".join(out) + "\n```"

def render_bullets(title: str, bullets: List[str], takeaway: str = "", cautions: List[str] = None) -> str:
    cautions = cautions or []
    out = []
    if title:
        out.append(title)
    for b in bullets:
        out.append(f"- {truncate(b, 120)}")

    if takeaway:
        out.append("")
        out.append(f"Takeaway: {takeaway}")

    for c in cautions:
        out.append(f"- {c}")

    return "```txt\n" + "\n".join(out) + "\n```"

def render_discord_response(payload: Dict[str, Any]) -> str:
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

# Example usage
example = {
    "format": "table",
    "title": "Best build options",
    "headers": ["Build", "Score", "Cost", "Note"],
    "rows": [
        ["Slayer A", "8.7", "Low", "Best balance"],
        ["Force C", "8.1", "High", "Highest burst"]
    ],
    "takeaway": "Slayer A is the safest pick.",
    "cautions": ["Force C costs more meseta."]
}

print(render_discord_response(example))
```

## Gợi ý nâng cấp

Nếu muốn chắc hơn nữa, bạn nên thêm một bước **validate JSON** trước khi render, để nếu LLM trả sai schema thì bot tự fallback sang bullets. Bạn cũng có thể giới hạn cứng: [youtube](https://www.youtube.com/watch?v=tjkAUxRHvk4)
- tối đa 5 cột,
- tối đa 6 rows,
- mỗi cell tối đa 24 ký tự cho table,
- mỗi value tối đa 60 ký tự cho kv. [stackoverflow](https://stackoverflow.com/questions/77820651/how-can-i-format-a-python-pretty-table-for-a-discord-message)

Điểm hay của flow này là LLM chỉ cần quyết định cấu trúc và nội dung, còn bot của bạn kiểm soát presentation hoàn toàn. Với bot game, đây là cách dễ maintain hơn nhiều so với việc để LLM “tự format đẹp” mỗi lần. [github](https://github.com/johnyob/Ascii-Table)

Nếu bạn muốn, bước tiếp theo mình có thể viết luôn cho bạn **phiên bản production-ready** gồm:
1. schema Pydantic/TypedDict,  
2. JSON validator,  
3. renderer có auto-wrap và fallback.# Project Progress (TODO List)

*This progress tracker is designed based on the Development Roadmap so the User can easily track it directly in the root directory.*

## Phase 1: Foundation (Core Initialization)
- [x] Architecture Design and Documentation (`docs/`)
- [x] Finalize RP Memory and Zero-Cost Strategy
- [x] Initialize project using `uv`
- [x] Setup Directory Structure (core, bot, data, ai_prompts...)
- [x] Install core packages (`discord.py`, `groq`, `google-generativeai`, `motor`)
- [x] Configure `.env` template (`.env.example`)
- [x] Write basic bot script (`main.py`) and run `/ping` command to test Discord connection.

## Phase 2: Knowledge & Scraper (The Knowledge)
- [x] Create `rag_pipeline.py` skeleton.
- [x] Write Scraper to download Class/Weapon data from Visiphone Wiki.
- [x] Upload wiki data to MongoDB and create text indexes.
- [x] Complete Search flow (Data Researcher Agent).

## Phase 3: Roleplay & Emotions (The Soul)
- [x] Setup Connection to MongoDB.
- [x] Write module to manage Long-term & Short-term Memory.
- [x] Write automatic Summarization mechanism (Context Compression).
- [x] Setup sample Persona files in `ai_prompts/characters/`.

## Phase 4: Fashion Recognition (The Eyes)
- [x] Integrate Gemini Vision.
- [x] Image noise reduction & Search keyword analysis.
- [x] Cross-check with Phashion database.

## Phase 5: Optimization & Expansion (Deployment)
- [ ] Complete Token Optimization (Fast Route, Intent Matrix).
- [ ] Write `Dockerfile` and test build using `uv`.
- [ ] Setup Grafana + Prometheus exporter in code.
- [ ] Deploy to Oracle Cloud / GCP.
