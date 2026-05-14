"""Deterministic wiki reply renderers.

These helpers sit between raw retrieved wiki evidence and the LLM. They handle
high-risk shapes where exact values or simple summaries should be rendered from
evidence directly instead of asking the model to infer them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


class DirectWikiReplyRenderer:
    """Render evidence-backed replies for cases that should avoid free generation."""

    def __init__(
        self,
        *,
        known_classes: Iterable[str],
        stopwords: Iterable[str],
        factual_query_hints: Iterable[str],
    ) -> None:
        self._known_classes = set(known_classes)
        self._stopwords = set(stopwords)
        self._factual_query_hints = set(factual_query_hints)

    def direct_class_overview_reply(self, question: str, extra_context: str) -> str | None:
        """Render broad class questions directly from retrieved chunks."""
        class_name = self._class_name_from_query(question)
        if not class_name:
            return None

        lower = question.lower()
        is_broad_class_query = (
            "class" in lower
            or any(h in lower for h in ("tell me about", "know more about", "overview", "describe"))
        )
        if not is_broad_class_query:
            return None

        game_match = re.search(r"Game version:\s*([^\n]+)", extra_context)
        game_label = game_match.group(1).strip() if game_match else "selected game"
        source_match = re.search(r"\[Source:\s*([^\]]+)\]", extra_context)
        source = source_match.group(1).strip() if source_match else ""

        overview_lines: list[str] = []
        weapon_lines: list[str] = []
        skill_names: list[str] = []

        for heading, body in context_blocks(extra_context):
            h = heading.lower()
            first = first_sentence(body)
            if not first:
                continue
            if "skills" in h:
                name_match = re.search(r"^##\s+(.+?)\s*$", body, re.MULTILINE)
                name = name_match.group(1).strip() if name_match else heading.split(">")[-1].strip()
                if name and name not in skill_names:
                    skill_names.append(name)
                continue
            if class_name.lower() in first.lower() or "class" in first.lower():
                if first not in overview_lines:
                    overview_lines.append(first)
                continue
            if "weapon" in h or "rifle" in h or "launcher" in h or "weapons" in body.lower():
                if first not in weapon_lines:
                    weapon_lines.append(first)

        lines = [f"**{class_name} Class ({game_label})**"]
        if overview_lines:
            lines.append("")
            lines.extend(f"- {line}" for line in overview_lines[:2])
        if weapon_lines:
            lines.append("")
            lines.append("**Weapons / combat notes from the wiki:**")
            lines.extend(f"- {line}" for line in weapon_lines[:3])
        if skill_names and "skill" in lower:
            lines.append("")
            lines.append("**Skills found in the retrieved data:**")
            lines.append("- " + ", ".join(skill_names[:8]))
        if not overview_lines and not weapon_lines and not (skill_names and "skill" in lower):
            return None
        if source:
            lines.append("")
            lines.append(f"Source: {source}")
        return "\n".join(lines)

    def direct_both_price_reply(self, question: str, extra_context: str) -> str | None:
        """Render deterministic Base/NGS price comparisons from table evidence."""
        if "[COMPARE_MODE]" not in extra_context:
            return None

        query_terms = price_query_terms(question)
        if not query_terms:
            return None

        blocks = split_compare_evidence(extra_context)
        base = find_price_match(blocks.get("Base", ""), query_terms)
        ngs = find_price_match(blocks.get("NGS", ""), query_terms)
        if not base and not ngs:
            return None

        title = " ".join(term.upper() if term == "ac" else term.title() for term in query_terms)
        lines = [f"**{title} AC Cost**", "", "```text"]
        lines.append("Game | Item | AC")
        lines.append("-----|------|---")
        if base:
            lines.append(f"Base | {base[0]} | {base[1]} AC")
        else:
            lines.append("Base | Not found in retrieved data | -")
        if ngs:
            lines.append(f"NGS  | {ngs[0]} | {ngs[1]} AC")
        else:
            lines.append("NGS  | Not found in retrieved data | -")
        lines.append("```")

        if base and ngs:
            if base[1] == ngs[1]:
                lines.append(f"Both Base and NGS show the same value: **{base[1]} AC**.")
            else:
                lines.append(f"They are different: **Base is {base[1]} AC**, while **NGS is {ngs[1]} AC**.")
        else:
            lines.append("Only one side had matching retrieved data, so I am not assuming the missing side.")
        return "\n".join(lines)

    def _class_name_from_query(self, question: str) -> str | None:
        """Extract a single mentioned class name from a question."""
        lower = question.lower()
        hits = [
            cls for cls in sorted(self._known_classes)
            if re.search(rf"\b{re.escape(cls)}\b", lower)
        ]
        return hits[0].title() if len(hits) == 1 else None


def context_blocks(extra_context: str) -> list[tuple[str, str]]:
    """Parse formatted wiki context into (heading, body) blocks."""
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", extra_context, re.MULTILINE))
    blocks: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(extra_context)
        heading = match.group(1).strip()
        body = extra_context[start:end].strip()
        if body:
            blocks.append((heading, body))
    return blocks


def first_sentence(text: str) -> str:
    """Return a compact first sentence from wiki chunk text."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"^#+\s*[^#\n]+", "", cleaned).strip()
    parts = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
    return parts[0].strip()


def split_compare_evidence(extra_context: str) -> dict[str, str]:
    """Return Base/NGS evidence blocks from compare-mode context."""
    blocks: dict[str, str] = {}
    pattern = re.compile(r"--- (Base|NGS) Evidence ---\n", re.MULTILINE)
    matches = list(pattern.finditer(extra_context))
    for idx, match in enumerate(matches):
        label = match.group(1)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(extra_context)
        blocks[label] = extra_context[start:end]
    return blocks


def markdown_table_rows(text: str) -> list[dict[str, str]]:
    """Parse simple markdown tables from retrieved context."""
    rows: list[dict[str, str]] = []
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if not (line.startswith("|") and idx + 1 < len(lines) and "---" in lines[idx + 1]):
            idx += 1
            continue

        headers = [cell.strip().lower() for cell in line.strip("|").split("|")]
        idx += 2
        while idx < len(lines) and lines[idx].strip().startswith("|"):
            cells = [cell.strip() for cell in lines[idx].strip().strip("|").split("|")]
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            rows.append(dict(zip(headers, cells[: len(headers)])))
            idx += 1
    return rows


def normalize_price(value: str) -> int | None:
    """Extract a numeric AC price from a table cell."""
    match = re.search(r"\d[\d,]*", value)
    if not match:
        return None
    return int(match.group(0).replace(",", ""))


def find_price_match(evidence: str, query_terms: list[str]) -> tuple[str, int] | None:
    """Find the best item-price row in one evidence block."""
    candidates: list[tuple[int, str, int]] = []
    for row in markdown_table_rows(evidence):
        name = next(
            (value for key, value in row.items() if key == "name" or key.endswith(":name")),
            "",
        )
        name_lower = name.lower()
        if not name_lower:
            continue

        missing = [
            term for term in query_terms
            if term.isdigit() and not re.search(rf"\b{re.escape(term)}\b", name_lower)
            or not term.isdigit() and term not in name_lower
        ]
        if missing:
            continue

        price_value = next(
            (
                value for key, value in row.items()
                if key in {"cost", "price", "ac"} or key.endswith(":cost") or key.endswith(":price")
            ),
            "",
        )
        price = normalize_price(price_value)
        if price is None:
            continue

        priority = -sum(1 for term in query_terms if term in name_lower)
        if any(marker in name_lower for marker in ("one time", "sale", "bonus", "+")):
            priority += 10
        if "pack" in name_lower or "x1" in name_lower:
            priority -= 1
        candidates.append((priority, name, price))

    if not candidates:
        return None
    _, name, price = sorted(candidates, key=lambda item: item[0])[0]
    return name, price


def price_query_terms(question: str) -> list[str]:
    """Extract item terms from an AC/cost/price question."""
    lower = question.lower()
    if not any(term in lower for term in ("ac", "cost", "price", "how much")):
        return []

    terms = []
    stop = {
        "alright", "tell", "about", "how", "much", "cost", "price", "ac",
        "is", "it", "the", "a", "an", "for", "of", "in", "base", "ngs",
        "both", "game", "games", "so", "does", "do",
    }
    for token in re.findall(r"[a-z0-9]+", lower):
        if token in stop:
            continue
        if len(token) < 3 and not token.isdigit():
            continue
        terms.append(token)
    return terms[:6]
