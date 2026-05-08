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
_NUMERIC_HINTS = {
    "price", "cost", "how much", "potency", "damage", "duration", "cooldown",
    "rate", "percent", "%", "days", "day", "pp", "hp", "bp",
}

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
- PSO2 Classic shop/system: ARKS_Cash_Shop, Swap_Shop, Treasure_Shop, Client_Orders, Enhancement, Dark_Blast
- PSO2 Classic armor: Arm_Units_List, Leg_Units_List, Back_Units_List

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

        # Step 5: Score retrieval quality and rank tables by query relevance
        quality = self._assess_retrieval_quality(query, primary_chunks, primary_tables)
        primary_tables = self._rank_tables(primary_tables, query)

        # Step 6: Format context
        return self._format_context(
            primary_chunks,
            primary_tables,
            game_mode,
            slug,
            query,
            quality,
        )

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
    def _query_tokens(query: str) -> set[str]:
        """Extract normalized query tokens for lightweight relevance scoring."""
        return {
            t for t in re.findall(r"[a-zA-Z0-9_+\-]{2,}", query.lower())
            if t not in {"the", "a", "an", "is", "are", "to", "for", "of", "in", "on", "and", "or"}
        }

    @classmethod
    def _query_phrases(cls, query: str) -> list[str]:
        """Build short phrase candidates (bigrams/trigrams) from query for stronger overlap checks."""
        words = [
            w for w in re.findall(r"[a-zA-Z0-9_+\-]{2,}", query.lower())
            if w not in {"the", "a", "an", "is", "are", "to", "for", "of", "in", "on", "and", "or"}
        ]
        phrases: list[str] = []
        for n in (3, 2):
            if len(words) < n:
                continue
            for i in range(len(words) - n + 1):
                phrases.append(" ".join(words[i:i + n]))
        return phrases[:8]

    @staticmethod
    def _is_numeric_query(query: str) -> bool:
        """Heuristic: does this query ask for numbers/stats/prices?"""
        q = query.lower()
        if any(h in q for h in _NUMERIC_HINTS):
            return True
        return bool(re.search(r"\b\d+(?:\.\d+)?\b", q))

    @classmethod
    def _table_score(cls, table: dict, query: str) -> int:
        """Score a table for ranking before formatting context."""
        score = 0
        tokens = cls._query_tokens(query)
        phrases = cls._query_phrases(query)
        numeric_query = cls._is_numeric_query(query)

        headers = [str(h).lower() for h in table.get("headers", [])]
        rows = table.get("rows", [])
        page_name = str(table.get("page_name", "")).lower()

        if numeric_query and any(h in {"cost", "price", "potency", "damage", "duration", "cooldown"} for h in headers):
            score += 5

        if any(tok in page_name for tok in tokens):
            score += 3
        if any(p in page_name for p in phrases):
            score += 6

        # Row overlap signal: prioritize tables that actually mention query entities.
        overlap_hits = 0
        numeric_hits = 0
        for row in rows[:40]:
            row_blob = " ".join(str(v).lower() for v in row.values())
            if any(tok in row_blob for tok in tokens):
                overlap_hits += 1
            if any(p in row_blob for p in phrases):
                overlap_hits += 3
            if numeric_query and re.search(r"\b\d+(?:\.\d+)?\b", row_blob):
                numeric_hits += 1

        score += min(overlap_hits, 6)
        if numeric_query:
            score += min(numeric_hits, 4)

        return score

    @classmethod
    def _rank_tables(cls, tables: list[dict], query: str) -> list[dict]:
        """Rank tables by relevance so critical rows appear before context budget runs out."""
        return sorted(tables, key=lambda t: cls._table_score(t, query), reverse=True)

    @classmethod
    def _assess_retrieval_quality(
        cls,
        query: str,
        chunks: list[dict],
        tables: list[dict],
    ) -> dict:
        """Assess retrieval quality and decide whether evidence is insufficient.

        This gate is generic and protects all factual domains, not just AC/pricing.
        """
        tokens = cls._query_tokens(query)
        phrases = cls._query_phrases(query)
        numeric_query = cls._is_numeric_query(query)

        token_hits = 0
        numeric_hits = 0
        phrase_hits = 0

        for c in chunks[:30]:
            blob = " ".join(
                [
                    str(c.get("page_title", "")).lower(),
                    str(c.get("section", "")).lower(),
                    str(c.get("sub_section", "")).lower(),
                    str(c.get("content", "")).lower(),
                ]
            )
            if any(t in blob for t in tokens):
                token_hits += 1
            if any(p in blob for p in phrases):
                phrase_hits += 1
            if numeric_query and re.search(r"\b\d+(?:\.\d+)?\b", blob):
                numeric_hits += 1

        for t in tables[:30]:
            headers_blob = " ".join(str(h).lower() for h in t.get("headers", []))
            rows_blob = " ".join(
                " ".join(str(v).lower() for v in row.values())
                for row in t.get("rows", [])[:30]
            )
            blob = headers_blob + " " + rows_blob
            if any(tok in blob for tok in tokens):
                token_hits += 2  # tables carry stronger factual signal
            if any(p in blob for p in phrases):
                phrase_hits += 2
            if numeric_query and re.search(r"\b\d+(?:\.\d+)?\b", blob):
                numeric_hits += 2

        score = token_hits + numeric_hits + phrase_hits
        insufficient = False
        reason = ""

        if numeric_query and numeric_hits == 0:
            insufficient = True
            reason = "numeric_query_without_numeric_evidence"
        elif phrases and phrase_hits == 0 and len(tokens) <= 5:
            insufficient = True
            reason = "no_phrase_overlap"
        elif token_hits == 0:
            insufficient = True
            reason = "no_entity_overlap"
        elif score < 3:
            insufficient = True
            reason = "weak_retrieval_signal"

        label = "weak" if insufficient else ("strong" if score >= 8 else "medium")
        return {
            "score": score,
            "label": label,
            "insufficient": insufficient,
            "reason": reason,
            "numeric_query": numeric_query,
        }

    @staticmethod
    def _format_context(
        chunks: list[dict],
        tables: list[dict],
        game_mode: str,
        slug: str | None,
        query: str,
        quality: dict,
    ) -> str:
        """Format chunks + tables into an LLM-friendly context string."""
        game_label = "PSO2: New Genesis" if game_mode == "NGS" else "PSO2 Classic"
        parts = []
        total_chars = 0
        numeric_query = bool(quality.get("numeric_query"))
        table_first = numeric_query

        table_budget = int(MAX_CONTEXT_CHARS * (0.72 if table_first else 0.40))
        text_budget = MAX_CONTEXT_CHARS - table_budget
        table_chars = 0
        text_chars = 0

        quality_line = (
            f"[RETRIEVAL_QUALITY] score={quality.get('score', 0)} "
            f"label={quality.get('label', 'unknown')} "
            f"reason={quality.get('reason', 'ok') or 'ok'}"
        )

        if quality.get("insufficient"):
            parts.append("[INSUFFICIENT_EVIDENCE]")
        parts.append(quality_line)

        parts.append(
            f"--- Wiki Data (from MongoDB) ---\n"
            f"Game version: {game_label}\n"
            f"IMPORTANT: Base your answer ONLY on the following wiki data.\n"
            f"If the data does not contain the answer, say so honestly.\n"
        )

        def append_tables() -> None:
            nonlocal total_chars, table_chars
            if not tables or table_chars >= table_budget or total_chars >= MAX_CONTEXT_CHARS:
                return
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

                if table_chars + len(entry) > table_budget or total_chars + len(entry) > MAX_CONTEXT_CHARS:
                    break
                parts.append(entry)
                table_chars += len(entry)
                total_chars += len(entry)

        def append_chunks() -> None:
            nonlocal total_chars, text_chars
            if not chunks or text_chars >= text_budget or total_chars >= MAX_CONTEXT_CHARS:
                return
            parts.append("\n## Text Content\n")
            for c in chunks:
                section = c.get("section", "")
                sub = c.get("sub_section", "")
                heading = section
                if sub and sub != section:
                    heading += f" > {sub}"
                content = c.get("content", "")

                entry = f"### {heading}\n{content}\n"
                if text_chars + len(entry) > text_budget or total_chars + len(entry) > MAX_CONTEXT_CHARS:
                    break
                parts.append(entry)
                text_chars += len(entry)
                total_chars += len(entry)

        if table_first:
            append_tables()
            append_chunks()
        else:
            append_chunks()
            append_tables()

        # Source attribution
        if slug:
            url = f"https://pso2na.arks-visiphone.com/wiki/{slug}"
            parts.append(f"\n[Source: {url}]")

        return "\n".join(parts)
