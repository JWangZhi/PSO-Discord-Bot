"""
Wiki Search Service — MongoDB-based retrieval for PSO2/NGS wiki data.

Strategy C (Hybrid): Gemini slug resolution + MongoDB text search.

Flow:
  1. Gemini resolves query → wiki page slug
  2. Map slug → page_name in wiki_pages
  3. Fetch all chunks + tables for matched page
  4. $text search for additional cross-page results
  5. Merge, deduplicate, format context for LLM
"""

import logging
import re

from google import genai
from google.genai import types
from motor.motor_asyncio import AsyncIOMotorDatabase

from core.db import MongoDB, COL_WIKI_CHUNKS, COL_WIKI_TABLES, COL_WIKI_PAGES
from settings import env as config
from settings import app as app_settings

log = logging.getLogger(__name__)

# Max context chars to feed to the LLM
MAX_CONTEXT_CHARS = 6000
MAX_TEXT_SEARCH_RESULTS = 10

# ---------------------------------------------------------------------------
# Slug → page_name mapping helpers
# ---------------------------------------------------------------------------

def slug_to_page_name(slug: str) -> str:
    """Convert a wiki slug to a wiki_raw page_name.

    Portal:New_Genesis/Slayer → Portal_New_Genesis_Slayer
    Hunter                    → Hunter
    """
    return slug.replace(":", "_").replace("/", "_")


def slug_to_game_mode(slug: str) -> str:
    """Infer game_mode from slug pattern."""
    if slug.startswith("Portal:New_Genesis") or slug.startswith("Portal_New_Genesis"):
        return "NGS"
    return "PSO2"


# ---------------------------------------------------------------------------
# Slug resolution prompt (reused from mcp_client.py)
# ---------------------------------------------------------------------------

_SLUG_SYSTEM_PROMPT = """\
You are a URL resolver for the PSO2 Global Arks-Visiphone wiki (pso2na.arks-visiphone.com).

TASK: Given a user question, output the COMPLETE wiki page slug. The slug is the path after "/wiki/" in the URL.

FORMAT RULES:
- For NGS: always output "Portal:New_Genesis/PageName" — NEVER just "Portal:" or "Portal:New_Genesis" alone.
- For PSO2 Classic: output "PageName" directly (e.g. "Hunter", "Katanas_List").
- Use underscores instead of spaces.
- Output ONLY the slug on a single line. No quotes, no explanation, no URL.

WIKI PAGES YOU KNOW:
- Classes: Portal:New_Genesis/Hunter, Portal:New_Genesis/Fighter, Portal:New_Genesis/Ranger, Portal:New_Genesis/Gunner, Portal:New_Genesis/Force, Portal:New_Genesis/Techter, Portal:New_Genesis/Braver, Portal:New_Genesis/Bouncer, Portal:New_Genesis/Waker, Portal:New_Genesis/Slayer
- Weapons: Portal:New_Genesis/Swords_List, Portal:New_Genesis/Katanas_List, Portal:New_Genesis/Assault_Rifles_List, Portal:New_Genesis/Launchers_List, etc.
- Systems: Portal:New_Genesis/Photon_Arts_List, Portal:New_Genesis/Enhancement, Portal:New_Genesis/Augments, Portal:New_Genesis/Class, Portal:New_Genesis/Experience_Level
- Skills: Portal:New_Genesis/Add-on_Skills, Portal:New_Genesis/Tech_Arts_Customization
- PSO2 Classic classes: Hunter, Fighter, Ranger, Gunner, Force, Techter, Braver, Bouncer, Summoner, Hero, Phantom, Etoile, Luster

EXAMPLES:
Q: "What skills does Slayer have?" game_version: ngs
Portal:New_Genesis/Slayer

Q: "best katanas" game_version: ngs
Portal:New_Genesis/Katanas_List

Q: "How does enhancement work?" game_version: ngs
Portal:New_Genesis/Enhancement

Q: "Hunter skills" game_version: pso2
Hunter

Q: "What are Photon Arts?" game_version: ngs
Portal:New_Genesis/Photon_Arts_List

Q: "addon skills potency level" game_version: ngs
Portal:New_Genesis/Add-on_Skills
"""


class WikiSearchService:
    """MongoDB-based wiki search with Gemini slug resolution."""

    def __init__(self):
        self._gemini = genai.Client(api_key=config.GEMINI_API_KEY)
        self._model = app_settings.ROUTER_MODEL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(self, query: str, game_version: str) -> str | None:
        """Search MongoDB wiki data for relevant context.

        Returns formatted context string, or None if nothing found.
        """
        db = MongoDB.get_db()
        chunks_col = db[COL_WIKI_CHUNKS]
        tables_col = db[COL_WIKI_TABLES]
        pages_col = db[COL_WIKI_PAGES]

        game_mode = "NGS" if game_version == "ngs" else "PSO2"

        # Step 1: Resolve query → page slug via Gemini
        slug = await self._resolve_slug(query, game_version)
        page_name = slug_to_page_name(slug) if slug else None

        primary_chunks = []
        primary_tables = []

        # Step 2: Fetch all data for the resolved page
        if page_name:
            page_doc = await pages_col.find_one({"_id": {"$regex": f":{page_name}$"}})
            if not page_doc:
                # Try partial match on page_name field
                page_doc = await pages_col.find_one({
                    "page_name": page_name,
                    "game_mode": game_mode,
                })

            if page_doc:
                pid = page_doc["_id"]
                gm = page_doc["game_mode"]
                cat = page_doc["category"]
                pn = page_doc["page_name"]

                log.info("[WikiSearch] Page resolved: %s → %s/%s/%s", slug, gm, cat, pn)

                # Fetch all chunks for this page
                cursor = chunks_col.find({
                    "game_mode": gm,
                    "category": cat,
                    "page_title": page_doc.get("page_title", pn.replace("_", " ")),
                })
                primary_chunks = await cursor.to_list(length=200)

                # Fetch all tables for this page
                cursor = tables_col.find({
                    "game_mode": gm,
                    "category": cat,
                    "page_name": pn,
                })
                primary_tables = await cursor.to_list(length=100)

                log.info("[WikiSearch] Primary: %d chunks, %d tables from %s",
                         len(primary_chunks), len(primary_tables), pn)
            else:
                log.info("[WikiSearch] Page not found in DB: %s (%s)", page_name, slug)

        # Step 3: Supplementary $text search for cross-page results
        text_chunks = []
        text_tables = []
        try:
            text_chunks = await self._text_search(
                chunks_col, query, game_mode, limit=MAX_TEXT_SEARCH_RESULTS
            )
            text_tables = await self._text_search(
                tables_col, query, game_mode, limit=5
            )
        except Exception as e:
            log.warning("[WikiSearch] Text search failed (may need indexes): %s", e)

        # Step 4: Merge and deduplicate
        seen_ids = {c["_id"] for c in primary_chunks}
        for c in text_chunks:
            if c["_id"] not in seen_ids:
                primary_chunks.append(c)
                seen_ids.add(c["_id"])

        seen_ids = {t["_id"] for t in primary_tables}
        for t in text_tables:
            if t["_id"] not in seen_ids:
                primary_tables.append(t)
                seen_ids.add(t["_id"])

        if not primary_chunks and not primary_tables:
            log.info("[WikiSearch] No results for query=%r game=%s", query, game_version)
            return None

        # Step 5: Format context
        return self._format_context(primary_chunks, primary_tables, game_mode, slug)

    # ------------------------------------------------------------------
    # Slug resolution
    # ------------------------------------------------------------------

    async def _resolve_slug(self, query: str, game_version: str) -> str | None:
        """Use Gemini to resolve a user query into a wiki page slug."""
        prompt = f"Q: {query}\ngame_version: {game_version}"
        try:
            response = await self._gemini.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SLUG_SYSTEM_PROMPT,
                    temperature=0.0,
                    max_output_tokens=120,
                ),
            )
            slug = (response.text or "").strip().strip('"\'` \n')

            # Clean up common Gemini artifacts
            if slug.startswith("http"):
                slug = slug.split("/wiki/", 1)[-1] if "/wiki/" in slug else ""
            if not slug or len(slug) < 2:
                return None
            if slug.rstrip("/").endswith(":") or slug.rstrip("/").endswith("New_Genesis"):
                return None

            log.info("[WikiSearch] Slug resolved: %r → %r", query, slug)
            return slug
        except Exception as exc:
            log.warning("[WikiSearch] Slug resolution failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Text search
    # ------------------------------------------------------------------

    @staticmethod
    async def _text_search(
        collection,
        query: str,
        game_mode: str,
        limit: int = 10,
    ) -> list[dict]:
        """Run MongoDB $text search filtered by game_mode."""
        cursor = collection.find(
            {"$text": {"$search": query}, "game_mode": game_mode},
            {"score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(limit)
        return await cursor.to_list(length=limit)

    # ------------------------------------------------------------------
    # Context formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_context(
        chunks: list[dict],
        tables: list[dict],
        game_mode: str,
        slug: str | None,
    ) -> str:
        """Format chunks + tables into an LLM-friendly context string."""
        game_label = "PSO2: New Genesis" if game_mode == "NGS" else "PSO2 Classic"
        parts = []
        total_chars = 0

        parts.append(
            f"--- Wiki Data (from MongoDB) ---\n"
            f"Game version: {game_label}\n"
            f"IMPORTANT: Base your answer ONLY on the following wiki data.\n"
            f"If the data does not contain the answer, say so honestly.\n"
        )

        # Format chunks (prose/descriptions)
        if chunks:
            parts.append("\n## Text Content\n")
            for c in chunks:
                section = c.get("section", "")
                sub = c.get("sub_section", "")
                heading = section
                if sub and sub != section:
                    heading += f" > {sub}"
                content = c.get("content", "")

                entry = f"### {heading}\n{content}\n"
                if total_chars + len(entry) > MAX_CONTEXT_CHARS:
                    break
                parts.append(entry)
                total_chars += len(entry)

        # Format tables (structured data)
        if tables and total_chars < MAX_CONTEXT_CHARS:
            parts.append("\n## Table Data\n")
            for t in tables:
                headers = t.get("headers", [])
                rows = t.get("rows", [])
                if not rows:
                    continue

                # Render as markdown table (compact)
                header_line = "| " + " | ".join(str(h) for h in headers[:8]) + " |"
                sep_line = "| " + " | ".join("---" for _ in headers[:8]) + " |"
                entry = f"{header_line}\n{sep_line}\n"

                for row in rows[:20]:  # cap at 20 rows per table
                    vals = []
                    for h in headers[:8]:
                        v = row.get(h, "")
                        s = str(v).strip()
                        if s.lower() == "nan":
                            s = ""
                        vals.append(s[:50])  # truncate long cells
                    entry += "| " + " | ".join(vals) + " |\n"

                if total_chars + len(entry) > MAX_CONTEXT_CHARS:
                    break
                parts.append(entry)
                total_chars += len(entry)

        # Source attribution
        if slug:
            url = f"https://pso2na.arks-visiphone.com/wiki/{slug}"
            parts.append(f"\n[Source: {url}]")

        return "\n".join(parts)
