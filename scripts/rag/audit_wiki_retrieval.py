"""Audit retrieval quality across wiki pages to detect hallucination risk.

Modes:
- offline (default): no Gemini calls, audits all pages deterministically from DB content.
- e2e: full WikiSearch.search path (includes slug resolver / API quota effects).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

import certifi
from motor.motor_asyncio import AsyncIOMotorClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.wiki_search import WikiSearchService

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_+\-]{3,}")
_QUALITY_RE = re.compile(r"\[RETRIEVAL_QUALITY\]\s*score=(\d+)\s+label=(\w+)\s+reason=([^\n]+)")


@dataclass
class AuditRow:
    game_mode: str
    category: str
    page_name: str
    page_title: str
    query: str
    has_context: bool
    insufficient: bool
    quality_score: int
    quality_label: str
    quality_reason: str
    token_overlap: bool
    risk: str


def _tokens(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "what", "how", "are", "is",
        "new", "genesis", "portal", "list", "guide", "tips", "info", "about",
    }
    return {t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in stop}


def _parse_quality(ctx: str) -> tuple[int, str, str]:
    m = _QUALITY_RE.search(ctx)
    if not m:
        return 0, "missing", "missing_quality_line"
    return int(m.group(1)), m.group(2), m.group(3).strip()


def _overlap_expected(page_title: str, ctx: str) -> bool:
    expected = _tokens(page_title)
    if not expected:
        return True
    blob = ctx.lower()
    hits = sum(1 for t in expected if t in blob)
    return hits >= 1


def _risk_level(has_context: bool, insufficient: bool, quality_label: str, overlap: bool) -> str:
    if not has_context or insufficient:
        return "high"
    if quality_label in {"weak", "missing"} or not overlap:
        return "medium"
    return "low"


async def _fetch_pages(db) -> list[dict]:
    cursor = db["wiki_pages"].find(
        {},
        {
            "game_mode": 1,
            "category": 1,
            "page_name": 1,
            "page_title": 1,
        },
    ).sort([("game_mode", 1), ("category", 1), ("page_name", 1)])
    return await cursor.to_list(length=10000)


def _build_queries(page_title: str, template: str) -> list[str]:
    if template == "title":
        return [page_title]
    if template == "natural":
        return [f"what is {page_title}"]
    # all
    return [page_title, f"what is {page_title}"]


def _normalized_page_title(page: dict) -> str:
    """Return a user-like page title instead of internal portal-style names."""
    page_title = (page.get("page_title") or "").strip()
    page_name = (page.get("page_name") or "").strip()

    if page_name.startswith("Portal_New_Genesis_"):
        cleaned = page_name.replace("Portal_New_Genesis_", "").replace("_", " ").strip()
        if cleaned:
            return cleaned

    if page_title:
        return page_title

    return page_name.replace("_", " ").strip()


def _chunk_title_candidates(page: dict) -> list[str]:
    """Build possible chunk page_title variants for a wiki_pages record."""
    cands = set()
    page_title = (page.get("page_title") or "").strip()
    page_name = (page.get("page_name") or "").strip()

    if page_title:
        cands.add(page_title)
    if page_name:
        cands.add(page_name.replace("_", " "))
    if page_name.startswith("Portal_New_Genesis_"):
        cands.add(page_name.replace("Portal_New_Genesis_", "").replace("_", " "))
    if "_" in page_name:
        cands.add(page_name.split("_")[-1])

    return [c for c in cands if c]


def _classify_row(page: dict, query: str, ctx: str | None) -> AuditRow:
    page_title = page.get("page_title") or page.get("page_name", "")
    has_context = bool(ctx)
    insufficient = "[INSUFFICIENT_EVIDENCE]" in (ctx or "")
    score, label, reason = _parse_quality(ctx or "")
    overlap = _overlap_expected(page_title, ctx or "")
    risk = _risk_level(has_context, insufficient, label, overlap)

    return AuditRow(
        game_mode=page.get("game_mode", "PSO2"),
        category=page.get("category", "unknown"),
        page_name=page.get("page_name", ""),
        page_title=page_title,
        query=query,
        has_context=has_context,
        insufficient=insufficient,
        quality_score=score,
        quality_label=label,
        quality_reason=reason,
        token_overlap=overlap,
        risk=risk,
    )


async def run_audit(
    max_pages: int | None,
    template: str,
    retry: int,
    mode: str,
) -> tuple[list[AuditRow], dict]:
    load_dotenv(dotenv_path=".env")
    uri = os.getenv("MONGODB_URI", "")
    db_name = os.getenv("APP_DB_NAME", "pso2_bot")

    client = AsyncIOMotorClient(uri, tlsCAFile=certifi.where())
    db = client[db_name]
    pages = await _fetch_pages(db)
    svc = WikiSearchService()

    if max_pages is not None:
        pages = pages[:max_pages]

    rows: list[AuditRow] = []
    by_risk = Counter()
    by_category = defaultdict(Counter)

    for i, page in enumerate(pages, start=1):
        game_mode = page.get("game_mode", "PSO2")
        game_version = "ngs" if game_mode == "NGS" else "pso2"
        query_title = _normalized_page_title(page)
        queries = _build_queries(query_title, template)

        for query in queries:
            ctx = None

            if mode == "offline":
                # Deterministic audit: bypass slug resolver and inspect formatting/gating behavior
                title_candidates = _chunk_title_candidates(page)
                chunks = await db["wiki_chunks"].find(
                    {
                        "game_mode": game_mode,
                        "category": page.get("category"),
                        "page_title": {"$in": title_candidates},
                    }
                ).to_list(length=300)
                tables = await db["wiki_tables"].find(
                    {
                        "game_mode": game_mode,
                        "category": page.get("category"),
                        "page_name": page.get("page_name"),
                    }
                ).to_list(length=200)

                if chunks or tables:
                    quality = svc._assess_retrieval_quality(query, chunks, tables)
                    ranked_tables = svc._rank_tables(tables, query)
                    ctx = svc._format_context(chunks, ranked_tables, game_mode, None, query, quality)
            else:
                # Full pipeline audit: includes LLM slug resolution + text search
                last_exc = None
                for _ in range(max(1, retry + 1)):
                    try:
                        ctx = await svc.search(query, game_version)
                        last_exc = None
                        break
                    except Exception as exc:  # pylint: disable=broad-except
                        last_exc = exc
                        await asyncio.sleep(0.8)
                if last_exc:
                    ctx = None

            row = _classify_row(page, query, ctx)
            rows.append(row)
            by_risk[row.risk] += 1
            by_category[f"{game_mode}/{row.category}"][row.risk] += 1

        if i % 20 == 0:
            print(f"[progress] {i}/{len(pages)} pages audited")

    summary = {
        "mode": mode,
        "total": len(rows),
        "risk": dict(by_risk),
        "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
        "high_risk_pages": [asdict(r) for r in rows if r.risk == "high"][:200],
        "medium_risk_pages": [asdict(r) for r in rows if r.risk == "medium"][:200],
    }
    client.close()
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit wiki retrieval quality across all pages")
    parser.add_argument("--mode", choices=["offline", "e2e"], default="offline")
    parser.add_argument("--max-pages", type=int, default=None, help="Limit number of pages (default: all)")
    parser.add_argument("--template", choices=["title", "natural", "all"], default="all")
    parser.add_argument("--retry", type=int, default=1, help="Retry count for transient model errors")
    parser.add_argument("--output", default="/tmp/wiki_retrieval_audit.json")
    args = parser.parse_args()

    rows, summary = asyncio.run(run_audit(args.max_pages, args.template, args.retry, args.mode))

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== Retrieval Audit Summary ===")
    print(f"Mode: {summary['mode']}")
    print(f"Total pages audited: {summary['total']}")
    print(f"Risk counts: {summary['risk']}")
    print("Top categories with high risk:")
    cat_rank = sorted(
        summary["by_category"].items(),
        key=lambda kv: kv[1].get("high", 0),
        reverse=True,
    )
    for k, v in cat_rank[:12]:
        if v.get("high", 0) == 0 and v.get("medium", 0) == 0:
            continue
        print(f"  {k}: high={v.get('high', 0)}, medium={v.get('medium', 0)}, low={v.get('low', 0)}")

    print(f"\nDetailed JSON report: {args.output}")


if __name__ == "__main__":
    main()
