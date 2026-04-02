"""
MCP Bridge — Fetches wiki content via MediaWiki action=parse API.
No browser dependency — lightweight async HTTP only.
"""

import logging
from urllib.parse import quote, urlparse

import aiohttp
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

from settings import env as config
from settings import app as app_settings

log = logging.getLogger(__name__)

# Domains the bot is allowed to fetch (security allowlist)
ALLOWED_DOMAINS = [
    "pso2.arks-visiphone.com",
    "pso2na.arks-visiphone.com",
    "bumped.org",
]

WIKI_BASE = "https://pso2na.arks-visiphone.com"
API_BASE = "https://pso2na.arks-visiphone.com/api.php"
HEADERS = {"User-Agent": "PSO2-Bot/1.0 (research only)"}

DEFAULT_MAX_LENGTH = 8000  # chars – keeps token usage reasonable

# Entity-Game validation maps
PSO2_ONLY_CLASSES = {"phantom", "hero", "etoile", "luster", "summoner"}
NGS_ONLY_CLASSES = {"slayer", "waker"}


def validate_entity_game(query: str, game_version: str) -> tuple[str, str | None]:
    """Check if the queried entity exists in the specified game version.

    Returns:
        (corrected_game_version, warning_message)
        warning_message is None if no correction needed.
    """
    query_lower = query.lower()

    if game_version == "ngs":
        for cls in PSO2_ONLY_CLASSES:
            if cls in query_lower:
                return "pso2", (
                    f"**Note:** {cls.title()} is a PSO2 Classic class and does not exist in NGS. "
                    f"Searching PSO2 Classic data instead.\n\n"
                )
    elif game_version == "pso2":
        for cls in NGS_ONLY_CLASSES:
            if cls in query_lower:
                return "ngs", (
                    f"**Note:** {cls.title()} is an NGS-only class and does not exist in PSO2 Classic. "
                    f"Searching NGS data instead.\n\n"
                )

    return game_version, None

_WIKI_SLUG_SYSTEM_PROMPT = """\
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
"""


class MCPBridge:
    """Async bridge to MediaWiki API (no browser needed)."""

    def __init__(self):
        self._ready = False
        self._gemini = genai.Client(api_key=config.GEMINI_API_KEY)
        self._gemini_model = app_settings.ROUTER_MODEL

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """No-op — Chrome is no longer needed."""
        self._ready = True
        log.info("[MCP] MediaWiki API mode — no Chrome needed.")

    async def close(self) -> None:
        """No-op — no Chrome to shut down."""
        self._ready = False
        log.info("[MCP] Bridge closed.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_url(self, url: str, *, max_length: int = DEFAULT_MAX_LENGTH) -> str:
        """Fetch wiki page content via MediaWiki action=parse API."""
        if not self._is_allowed(url):
            raise ValueError(
                f"Domain not in allowlist. Allowed: {', '.join(ALLOWED_DOMAINS)}"
            )

        # Extract page slug from URL
        slug = ""
        if "/wiki/" in url:
            slug = url.split("/wiki/", 1)[-1]
        if not slug:
            raise ValueError(f"Cannot extract page slug from URL: {url}")

        async with aiohttp.ClientSession() as session:
            params = {
                "action": "parse",
                "page": slug,
                "format": "json",
                "prop": "text|displaytitle",
            }
            async with session.get(
                API_BASE,
                params=params,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"MediaWiki API returned HTTP {resp.status}")
                data = await resp.json()

        if "error" in data:
            raise RuntimeError(
                f"MediaWiki API error: {data['error'].get('info', 'unknown')}"
            )

        html = data.get("parse", {}).get("text", {}).get("*", "")
        if not html:
            raise RuntimeError(f"Empty content from MediaWiki API for slug: {slug}")

        # Clean HTML → plain text
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "style", "nav"]):
            tag.decompose()
        for el in soup.find_all("span", class_="mw-editsection"):
            el.decompose()
        for el in soup.find_all("div", id="toc"):
            el.decompose()
        for el in soup.find_all("table", class_="navbox"):
            el.decompose()

        text = soup.get_text(separator="\n", strip=True)
        return text[:max_length]

    async def search_wiki(self, query: str, game_version: str = "ngs") -> str | None:
        """Search the PSO2 wiki and return page content from the best match.

        Uses Gemini to resolve query → wiki page slug, then Chrome to fetch the page.
        Returns None if resolution or fetch fails (caller should fallback).
        """
        if not self._ready:
            return None

        slug = await self._resolve_wiki_slug(query, game_version)
        if not slug:
            return None

        page_url = f"{WIKI_BASE}/wiki/{quote(slug, safe='/:_')}"
        log.info("[MCP] Wiki search — query=%r  slug=%r  url=%s", query, slug, page_url)

        try:
            content = await self.fetch_url(page_url)
            if content and len(content.strip()) > 100:
                return f"[Source: {page_url}]\n\n{content}"
            log.info("[MCP] Page fetched but content too short (%d chars)",
                     len(content.strip()) if content else 0)
        except Exception as exc:
            log.warning("[MCP] Wiki page fetch failed for %s: %s", page_url, exc)

        return None

    # ------------------------------------------------------------------
    # Wiki helpers
    # ------------------------------------------------------------------

    async def _resolve_wiki_slug(self, query: str, game_version: str) -> str | None:
        """Use Gemini to resolve a user question into the best wiki page slug."""
        prompt = f"Q: {query}\ngame_version: {game_version}"
        log.info("[MCP] Resolving slug for query=%r game=%s", query, game_version)
        try:
            response = await self._gemini.aio.models.generate_content(
                model=self._gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_WIKI_SLUG_SYSTEM_PROMPT,
                    temperature=0.0,
                    max_output_tokens=120,
                ),
            )
            slug = (response.text or "").strip()
            log.info("[MCP] Gemini raw response: %r", slug)
            slug = slug.strip('"\'` \n')
            if slug.startswith("http"):
                slug = slug.split("/wiki/", 1)[-1] if "/wiki/" in slug else ""
            if not slug or len(slug) < 2:
                log.warning("[MCP] Slug resolution returned empty/too-short: %r", slug)
                return None
            if slug.rstrip("/").endswith(":") or slug.rstrip("/").endswith("New_Genesis"):
                log.warning("[MCP] Slug resolution returned incomplete portal path: %r", slug)
                return None
            log.info("[MCP] Slug resolved: %r -> %r", query, slug)
            return slug
        except Exception as exc:
            log.warning("[MCP] Slug resolution failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _is_allowed(url: str) -> bool:
        """Check URL against the domain allowlist."""
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        return any(hostname == d or hostname.endswith(f".{d}") for d in ALLOWED_DOMAINS)
