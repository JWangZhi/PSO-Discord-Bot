# Critical Fixes Implementation Plan

> **Purpose:** Self-contained implementation spec for another LLM to execute code changes. Contains all context, decisions, file paths, and concrete code examples needed.
>
> **Date:** 2026-04-01
> **Decisions confirmed by owner:**
> - Keep LM Studio for embeddings (free, local)
> - Use Qwen3-Embedding-0.6B (GGUF) for both batch + runtime
> - LM Studio stays running 24/7 (0.6B = 0.7GB RAM, idle = negligible power)
> - VisionAgent switches from local VLM to Gemini Vision (free tier)
> - MCP Chrome replaced with direct MediaWiki API

---

## Project Overview

PSO2/NGS AI Discord Bot with:
- **RouterAgent** (`core/agents/router_agent.py`) — Gemini Flash, classifies intent
- **ChatAgent** (`core/agents/chat_agent.py`) — Groq/Llama, generates replies
- **VisionAgent** (`core/agents/vision_agent.py`) — local VLM (BROKEN), analyzes outfit images
- **MemoryManager** (`core/memory.py`) — MongoDB 2-layer memory
- **MCPBridge** (`core/mcp/mcp_client.py`) — Chrome DevTools wiki fetch (BROKEN)
- **WikiSearchService** (`core/wiki_search.py`) — MongoDB-based retrieval with Gemini slug resolution
- **WikiScraper** (`data/scrapers/wiki_scraper.py`) — offline MediaWiki scraper
- **ContextCompressor** (`core/context_compressor.py`) — Groq summarizer (exists but never called)
- **Entry point:** `main.py`
- **Settings:** `settings/env.py` (secrets), `settings/app.py` (operational), `settings/scraper.py` (scraper)

---

## 6 Critical Issues

| # | Issue | Root Cause | Impact |
|---|---|---|---|
| 1 | RAG broken | 5 config vars undefined, never imported in `main.py` | Bot can't use cached knowledge |
| 2 | MCP can't access wiki | Chrome headless blocked by CloudFlare | Wiki queries fail silently |
| 3 | Embedding mismatch | Chunks are corrupted (see #6), not the model | RAG returns irrelevant results |
| 4 | VisionAgent never works | Depends on local VLM at `localhost:9707` | `/fashion` command always fails |
| 5 | MCP bad UX | Chrome startup ~5-10s, then blocked | Users wait and get nothing |
| 6 | HTML extraction corrupts data | pandas can't parse rowspan/colspan tables; text chunks flatten all skills into one blob | Garbage in → garbage out for RAG |
| 7 | Game type not passed from @mention flow | `on_message` hardcodes `"ngs"` at line 208 — no game version selection for mentions | All @mention wiki queries assume NGS |
| 8 | No entity-game validation | Slug resolver doesn't know which classes/entities exist in which game | Bot tries to fetch "Phantom" as NGS page (doesn't exist), fails silently, hallucinates |
| 9 | Hallucination on fallback | When wiki fetch returns nothing, `build_no_rag_context()` tells LLM to "answer from general model knowledge" — Groq hallucinates game data | Bot invents skills like "Phantom Mark", "Shadow Step" that don't exist |
| 10 | Old hallucinated responses contaminate memory | Previous wrong answers stored in MongoDB `recent_messages` get injected into every new Groq prompt | Bot sees its own old hallucinated answers and may repeat them |
| 11 | `--debug` floods terminal with pymongo noise | `logging.basicConfig(level=DEBUG)` sets ALL loggers to DEBUG — pymongo heartbeats every 10s produce walls of text | Real bot logs are unreadable |
| 12 | `on_message` reply crashes when > 2000 chars | `message.reply()` has no length splitting unlike `send_long_message()` for slash commands | Bot silently fails on long wiki responses via @mention |
| 13 | Follow-up queries fail — no query expansion | RAG receives raw "can you more details about it?" with no context resolution | Pronouns/references return zero results from text search |
| 14 | `reembed_from_cache.py` reads wrong source | Script re-extracts from HTML `cache/` folder instead of using clean `.chunks.json` in `wiki_raw/` | MongoDB gets stale/corrupted chunks even though clean ones exist |

---

## Task Checklist

### Phase 1: Replace MCP Chrome with MediaWiki API [P0]
- [ ] Rewrite `MCPBridge.fetch_url()` to use `aiohttp` + MediaWiki `action=parse`
- [ ] Keep `MCPBridge.search_wiki()` interface unchanged
- [ ] Remove Chrome DevTools dependency (mcp, stdio_client imports)
- [ ] `start()` and `close()` become lightweight no-ops
- [ ] Keep `_resolve_wiki_slug()` (Gemini-based) unchanged
- [ ] Keep `_is_allowed()` domain check
- [ ] Add `aiohttp` to `pyproject.toml` dependencies
- [ ] Remove `selenium` and `webdriver-manager` from `pyproject.toml`
- [ ] Test: fetch `Portal:New_Genesis/Slayer` via new API

### Phase 2: Fix RAG Config and Integration [P0]
- [x] Add to `settings/env.py`: `LOCAL_EMBED_URL`, `LOCAL_EMBED_MODEL` (Pinecone removed)
- [x] Add to `settings/app.py`: `RAG_WIKI_INDEX_NAME`, `RAG_MIN_CHUNK_CONFIDENCE`, `RAG_ENABLED`
- [ ] Add to `.env.example`: all new variables with defaults
- [x] In `main.py`, optionally import and initialize `RAGPipeline` when `RAG_ENABLED=true`
- [x] Add fallback chain in wiki_search flow: RAG → MediaWiki API → model knowledge
- [x] Extract shared wiki-fetch logic into a method (deduplicate `on_message` + `/ask` command)

### Phase 2.5: Fix HTML Extraction Pipeline [P0]
- [x] Clean per-skill chunks already exist in `data/storage/wiki_raw/**/*.chunks.json` (verified)
- [ ] ~~Rewrite skill table extraction~~ (NOT NEEDED — wiki_scraper already produces good chunks)
- [ ] Re-embed from `wiki_raw/` chunks (not HTML cache) — see Issue 14 fix below
- [x] ~~Clear Pinecone index~~ (REMOVED — Pinecone no longer used, MongoDB is sole DB)

> **HISTORICAL NOTE (now resolved):**
> Pinecone was removed from the project. All retrieval now uses MongoDB
> (WikiSearchService with Gemini slug resolution + $text search).
> The stale vector issue below is no longer relevant.
>
> | Query | Top Pinecone result | Score | Correct? |
> |---|---|---|---|
> | "show me all skill of Ranger class" | Sticky Bomb Quick Reload (1 random skill) | 0.63 | WRONG |
> | "list Ranger skills" | Class > Types of Skills (generic page) | 0.74 | WRONG |
> | "Slayer Gunblade Focus Overdrive" | Bouncer > Fanatic Blade Augment | 0.70 | WRONG CLASS |
> | "Phantom skills" | Hero > Properties of Hero | 0.70 | WRONG CLASS |



### Phase 3: Fix VisionAgent [P1]
- [ ] Replace local VLM (OpenAI client) with Gemini Vision API
- [ ] Make `analyze_outfit_from_bytes()` async
- [ ] Update callers in `main.py` (2 locations) to `await`
- [ ] Test with sample image

### Phase 4: Quick Wins [P1]
- [ ] Sanitize error messages — no raw exceptions to Discord users
- [ ] Fix `send_long_message` overflow on lines > 2000 chars
- [ ] Wire `ContextCompressor` into `ChatAgent.generate_reply()`
- [ ] Fix Dockerfile: add `COPY uv.lock .` before `RUN uv sync`
- [ ] Fix `docker-compose.yml`: externalize Grafana password

---

## Phase 1: MCP → MediaWiki API (Detailed Spec)

### File: `core/mcp/mcp_client.py`

**Remove these imports:**
```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
```

**Add these imports:**
```python
import aiohttp
from bs4 import BeautifulSoup
```

**Add constant:**
```python
API_BASE = "https://pso2na.arks-visiphone.com/api.php"
HEADERS = {"User-Agent": "PSO2-Bot/1.0 (research only)"}
```

**Remove:** `_EXTRACT_WIKI_JS` (entire JS snippet)

**Remove from `__init__`:** `self._session`, `self._exit_stack`, `self._ready` (Chrome session state)

**Keep:** `self._gemini`, `self._gemini_model` (for slug resolution)

**Rewrite `start()`:**
```python
async def start(self) -> None:
    """No-op — Chrome is no longer needed."""
    self._ready = True
    log.info("[MCP] MediaWiki API mode — no Chrome needed.")
```

**Rewrite `close()`:**
```python
async def close(self) -> None:
    """No-op — no Chrome to shut down."""
    self._ready = False
    log.info("[MCP] Bridge closed.")
```

**Rewrite `fetch_url()`:**
```python
async def fetch_url(self, url: str, *, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """Fetch wiki page content via MediaWiki action=parse API."""
    if not self._is_allowed(url):
        raise ValueError(f"Domain not in allowlist. Allowed: {', '.join(ALLOWED_DOMAINS)}")

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
        async with session.get(API_BASE, params=params, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"MediaWiki API returned HTTP {resp.status}")
            data = await resp.json()

    if "error" in data:
        raise RuntimeError(f"MediaWiki API error: {data['error'].get('info', 'unknown')}")

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
    # Remove navboxes
    for el in soup.find_all("table", class_="navbox"):
        el.decompose()

    text = soup.get_text(separator="\n", strip=True)
    return text[:max_length]
```

**`search_wiki()` stays the same** — it calls `_resolve_wiki_slug()` then `fetch_url()`.

### File: `pyproject.toml`

```diff
 dependencies = [
+    "aiohttp>=3.11.0",
     "beautifulsoup4>=4.14.3",
     ...
-    "mcp[cli]>=1.26.0",
     ...
-    "selenium>=4.41.0",
-    "webdriver-manager>=4.0.2",
 ]
```

> **Note:** Keep `mcp` in dependencies only if used elsewhere. If `mcp_client.py` is the only consumer, remove it.

---

## Phase 2: RAG Config + Integration (Detailed Spec)

### File: `settings/env.py`

Add after line 24:
```python
# RAG / Embeddings (LM Studio local)
# (Pinecone removed — no longer needed)
LOCAL_EMBED_URL = os.getenv("LOCAL_EMBED_URL", "http://127.0.0.1:9707/v1")
LOCAL_EMBED_URL = os.getenv("LOCAL_EMBED_URL", "http://127.0.0.1:9707/v1")
LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", "qwen3-embedding-0.6b")
```

### File: `settings/app.py`

Add after line 33:
```python
# RAG
RAG_ENABLED = os.getenv("RAG_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
RAG_WIKI_INDEX_NAME = os.getenv("RAG_WIKI_INDEX_NAME", "pso2-wiki")
RAG_MIN_CHUNK_CONFIDENCE = float(os.getenv("RAG_MIN_CHUNK_CONFIDENCE", "0.65"))
```

### File: `main.py`

**In `PSO2Bot.__init__`**, add RAG initialization:
```python
from settings import app as app_settings

# RAG Pipeline (optional — uses MongoDB WikiSearchService)
self.rag = None
if app_settings.RAG_ENABLED:
    try:
        from data.rag.rag_pipeline import RAGPipeline
        self.rag = RAGPipeline()
        print("[OK] RAG Pipeline initialized.")
    except Exception as e:
        print(f"[WARN] RAG Pipeline init failed (bot continues): {e}")
```

**Extract shared wiki search method:**
```python
async def _retrieve_wiki_context(self, query: str, game_version: str, game_label: str) -> str:
    """Retrieve wiki context via RAG → MediaWiki API → model knowledge fallback chain."""
    # 1. Try RAG (cached knowledge)
    if self.rag:
        try:
            rag_context = await asyncio.to_thread(
                self.rag.retrieve_context, query, game_version.upper()
            )
            if "[INSUFFICIENT_EVIDENCE]" not in rag_context:
                return rag_context
        except Exception as e:
            print(f"[WARN] RAG retrieval failed: {e}")

    # 2. Fallback: Live wiki fetch via MediaWiki API
    if self.mcp:
        wiki_content = await self.mcp.search_wiki(query, game_version=game_version)
        if wiki_content:
            return (
                "--- Live Wiki Data ---\n"
                f"Game version: {game_label}\n"
                "IMPORTANT: Base your answer ONLY on the following wiki content.\n"
                "If sources are present, cite the source URL in your answer.\n\n"
                f"{wiki_content}\n"
            )

    # 3. Final fallback: model knowledge only
    return self.build_no_rag_context(game_label)
```

**Replace duplicated wiki logic** in both `on_message` (wiki_search handler) and `/ask` command with:
```python
extra = await self._retrieve_wiki_context(content, game_key, game_label)
```

---

## Phase 2.5: Fix HTML Extraction (Detailed Spec)

### Problem Evidence

Analyzed `data/storage/cache/Portal_New_Genesis_Slayer.html`:
- 25 tables total
- Tables 0-1: navigation/class grid (noise)
- **Tables 2-22: individual skill tables** — each has class `table-responsive-md`
- Tables 23-24: navboxes (noise)

Each skill table has a consistent structure:
```
Row 0: [Skill Name] [Description (colspan)]
Row 1: [Restriction / "Can only be used..." (colspan)]  — optional
Row 2: [Prerequisite - Skill Lv. X (colspan)]            — optional
Row 3: [Effect | Skill Level | header row]
Row 4: [Effect | 1 | 2 | 3 ...]                          — level numbers
Row 5+: [Stat Name | Value1 | Value2 ...]                — actual data
```

### File: `data/scrapers/wiki_scraper.py`

**Add new function — skill table detector:**
```python
def _is_skill_table(table: Tag) -> bool:
    """Detect skill data tables (have class table-responsive-md)."""
    classes = {str(c).lower() for c in table.get("class", [])}
    return "table-responsive-md" in classes and "wikitable" in classes
```

**Add new function — skill table extractor:**
```python
def _extract_skill_from_table(table: Tag) -> dict | None:
    """Extract structured skill data from a single wiki skill table.
    
    Returns dict with: skill_name, description, restriction, prerequisite, stats
    Returns None if table doesn't match expected skill table format.
    """
    rows = table.find_all("tr")
    if len(rows) < 3:
        return None

    # Row 0: Skill name (first cell) + Description (second cell, usually colspan)
    first_row_cells = rows[0].find_all(["td", "th"])
    if not first_row_cells:
        return None

    skill_name = first_row_cells[0].get_text(strip=True)
    description = ""
    if len(first_row_cells) > 1:
        description = first_row_cells[1].get_text(strip=True)

    # Scan for restriction and prerequisite (colspan rows before stat rows)
    restriction = ""
    prerequisite = ""
    stat_start_row = 1

    for i in range(1, len(rows)):
        cells = rows[i].find_all(["td", "th"])
        # Full-width rows (colspan) contain restriction or prerequisite
        if len(cells) == 1 and cells[0].get("colspan"):
            text = cells[0].get_text(strip=True)
            if "Prerequisite" in text:
                prerequisite = text
            elif "Can only" in text or "only be used" in text:
                restriction = text
            stat_start_row = i + 1
        else:
            break

    # Remaining rows: key-value stat pairs
    # Skip header rows ("Effect | Skill Level" and "Effect | 1 | 2 | 3...")
    stats = {}
    for row in rows[stat_start_row:]:
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True)
            if key in ("Effect", ""):
                continue
            values = [c.get_text(strip=True) for c in cells[1:] if c.get_text(strip=True)]
            if values:
                # Single value → string, multiple → show as "val1 / val2 / ..."
                stats[key] = values[0] if len(values) == 1 else " / ".join(values)

    if not skill_name:
        return None

    return {
        "skill_name": skill_name,
        "description": description,
        "restriction": restriction,
        "prerequisite": prerequisite,
        "stats": stats,
    }
```

**Add new function — format skill as embeddable text chunk:**
```python
def _skill_to_chunk_text(skill: dict) -> str:
    """Convert extracted skill dict into clean markdown for embedding."""
    lines = [f"## {skill['skill_name']}"]
    if skill["description"]:
        lines.append(skill["description"])
    if skill["restriction"]:
        lines.append(skill["restriction"])
    if skill["prerequisite"]:
        lines.append(skill["prerequisite"])
    if skill["stats"]:
        for key, val in skill["stats"].items():
            lines.append(f"- {key}: {val}")
    return "\n".join(lines)
```

**Modify `extract_text_chunks()` to handle skill tables:**

Replace the `_remove_noise_tables_for_text()` call with skill-aware logic:

```python
def extract_text_chunks(html, game_mode, category, page_path, page_title):
    soup = BeautifulSoup(html, "html.parser")

    # Clean noise (scripts, styles, edit links, TOC)
    for tag in soup.find_all(["script", "style", "nav"]):
        tag.decompose()
    for span in soup.find_all("span", class_="mw-editsection"):
        span.decompose()
    for toc in soup.find_all("div", id="toc"):
        toc.decompose()

    content_root = soup.find("div", class_="mw-parser-output") or soup
    chunks = []
    url = _page_url(page_path)

    # Pass 1: Extract skills from skill tables (before removing them)
    for table in content_root.find_all("table"):
        if _is_skill_table(table):
            skill = _extract_skill_from_table(table)
            if skill:
                text = _skill_to_chunk_text(skill)
                if len(text) >= MIN_CHUNK_LEN:
                    chunks.append({
                        "chunk_id": _chunk_id(url, "Skills", skill["skill_name"]),
                        "game_mode": game_mode.upper(),
                        "category": category,
                        "page_title": page_title,
                        "section": "Skills",
                        "sub_section": skill["skill_name"],
                        "content": text,
                        "url": url,
                        "last_scraped": _now_iso(),
                    })
            # Remove the table so it doesn't pollute prose chunks
            table.decompose()
        elif _is_nav_table(table):
            table.decompose()

    # Pass 2: Extract remaining prose by h2/h3 headings (existing logic)
    current_h2 = "Introduction"
    current_h3 = "Overview"
    buffer = []

    def flush(h2, h3, buf):
        # ... (keep existing flush logic unchanged)
        pass

    for element in content_root.children:
        # ... (keep existing heading-split logic unchanged)
        pass

    flush(current_h2, current_h3, buffer)
    return chunks
```

**Expected output for Slayer page:**

Before: 5 chunks (1 mega-blob of 5000+ chars for all skills)
After: ~25 chunks (1 per skill, ~150-300 chars each, clean markdown)

Example chunk:
```json
{
  "section": "Skills",
  "sub_section": "Gunblade Focus Overdrive",
  "content": "## Gunblade Focus Overdrive\n[Active Skill] Expend your entire full Focus Gauge to temporarily increase the effects of Gunblade Focus. Using the skill again while it is active will unleash a powerful attack.\nCan only be used with a Main Class/Gunblade.\nPrerequisite - Gunblade Focus Lv. 1\n- Effect Duration: 30 sec\n- Cooldown: 90 sec\n- Potency: 110%\n- PP Consumption: 80%\n- PP Recovery: 150%\n- Finisher Potency: 2450"
}
```

---

## Phase 3: Fix VisionAgent (Detailed Spec)

### File: `core/agents/vision_agent.py`

**Replace imports:**
```python
# Remove:
from openai import OpenAI

# Add:
from google import genai
from google.genai import types
from settings import env as config
```

**Replace `__init__`:**
```python
def __init__(self):
    self._client = genai.Client(api_key=config.GEMINI_API_KEY)
    self._model = "gemini-2.5-flash"
```

**Replace `analyze_outfit_from_bytes` (make async):**
```python
async def analyze_outfit_from_bytes(self, image_data: bytes, mime_type: str = "image/jpeg") -> dict:
    """Analyze outfit image using Gemini Vision API."""
    try:
        prompt = self._create_prompt()
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[
                prompt,
                types.Part.from_bytes(data=image_data, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=512,
            ),
        )
        import json
        text_response = response.text.strip()
        return json.loads(text_response)
    except json.JSONDecodeError as e:
        print(f"[VisionAgent] Failed to parse JSON: {e}")
        return {"error": "Invalid JSON response from AI"}
    except Exception as e:
        print(f"[VisionAgent] Error analyzing image: {e}")
        return {"error": str(e)}
```

**Keep `_create_prompt()` unchanged** — the prompt is good.

### File: `main.py`

**Update 2 call sites from sync to async:**

Line ~153 (in `on_message`, fashion_match handler):
```python
# Before:
tags_str = self.vision.analyze_outfit_from_bytes(img_bytes)
# After:
tags_str = await self.vision.analyze_outfit_from_bytes(img_bytes)
```

Line ~300 (in `/fashion` command):
```python
# Before:
tags_str = bot.vision.analyze_outfit_from_bytes(img_bytes)
# After:
tags_str = await bot.vision.analyze_outfit_from_bytes(img_bytes)
```

---

## Phase 4: Quick Wins (Detailed Spec)

### 4A: Sanitize Error Messages

**File: `main.py`, line ~195-197:**
```python
# Before:
except Exception as e:
    EXTERNAL_API_ERRORS.labels(service_name="router_llm").inc()
    await message.reply(f"❌ Error processing request: {e}")

# After:
except Exception as e:
    EXTERNAL_API_ERRORS.labels(service_name="router_llm").inc()
    print(f"[ERROR] Message processing failed for user {message.author.id}: {e}")
    await message.reply("❌ Something went wrong processing your request. Please try again.")
```

### 4B: Fix `send_long_message` Overflow

**File: `main.py`, in `send_long_message()`:**

Add handling for single lines exceeding 2000 chars:
```python
async def send_long_message(interaction: discord.Interaction, text: str):
    limit = app_settings.DISCORD_MESSAGE_LIMIT
    if len(text) <= limit:
        await interaction.followup.send(text)
        return

    lines = text.split("\n")
    current_chunk = ""

    for line in lines:
        # Handle lines longer than the limit
        while len(line) > limit:
            if current_chunk:
                await interaction.followup.send(current_chunk)
                current_chunk = ""
            await interaction.followup.send(line[:limit])
            line = line[limit:]

        if len(current_chunk) + len(line) + 1 > limit:
            await interaction.followup.send(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        await interaction.followup.send(current_chunk)
```

### 4B-2: Fix `message.reply()` Overflow in `on_message` (Issue 12)

**Problem:** `send_long_message()` only works with slash command interactions. The `on_message`
handler uses `message.reply()` directly — crashes when reply > 2000 chars with:
```
400 Bad Request: Must be 2000 or fewer in length.
```

**Fix:** Add a `reply_long` helper for regular messages, then use it in `on_message`:

**File: `main.py`** — add helper after `send_long_message`:
```python
async def reply_long(message: discord.Message, text: str):
    """Split a long reply into 2000-char chunks for message.reply()."""
    limit = app_settings.DISCORD_MESSAGE_LIMIT
    if len(text) <= limit:
        await message.reply(text)
        return

    lines = text.split("\n")
    current_chunk = ""
    first = True

    for line in lines:
        while len(line) > limit:
            if current_chunk:
                if first:
                    await message.reply(current_chunk)
                    first = False
                else:
                    await message.channel.send(current_chunk)
                current_chunk = ""
            if first:
                await message.reply(line[:limit])
                first = False
            else:
                await message.channel.send(line[:limit])
            line = line[limit:]

        if len(current_chunk) + len(line) + 1 > limit:
            if first:
                await message.reply(current_chunk)
                first = False
            else:
                await message.channel.send(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        if first:
            await message.reply(current_chunk)
        else:
            await message.channel.send(current_chunk)
```

**Then replace all `message.reply(reply)` calls in `on_message` with:**
```python
await reply_long(message, reply)
```

There are 4 locations in `on_message`:
- Line ~202: fashion_match with image
- Line ~204: fashion_match without image
- Line ~211: wiki_search
- Line ~217: chat

### 4C: Wire ContextCompressor

**File: `main.py`, in `PSO2Bot.__init__`:**
```python
from core.context_compressor import ContextCompressor
self.compressor = ContextCompressor()
```

**File: `core/agents/chat_agent.py`, in `generate_reply()`, after saving assistant reply:**
```python
# After line: await self.memory.add_message(user_id, role="assistant", content=reply)
# Trigger compression check (non-blocking)
# Note: The caller (PSO2Bot) should handle this, or pass compressor to ChatAgent
```

Alternatively, trigger in `main.py` after getting the reply:
```python
reply = await self.chat_agent.generate_reply(session_id, content, extra_context=extra)
# Check if compression is needed (non-blocking)
asyncio.create_task(self.compressor.run_compression(session_id))
```

### 4D: Fix Dockerfile

```dockerfile
# Before:
COPY pyproject.toml .

# After:
COPY pyproject.toml uv.lock ./
```

### 4E: Fix docker-compose.yml Grafana Password

```yaml
# Before:
- GF_SECURITY_ADMIN_PASSWORD=admin

# After:
- GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}
```

---

## Embedding Model Decision

**Model:** `Qwen3-Embedding-0.6B` (GGUF quantized)
**LM Studio config:**
- Search: `Qwen/Qwen3-Embedding-0.6B-GGUF` in LM Studio
- Mode: Embedding (not Chat)
- Endpoint: `http://127.0.0.1:9707/v1` (or configure separate port)
- Runs 24/7 — 0.7GB RAM, idle = negligible power draw

**Settings in `.env`:**
```
LOCAL_EMBED_URL=http://127.0.0.1:9707/v1
LOCAL_EMBED_MODEL=qwen3-embedding-0.6b
```

**After extraction fix:** re-run scraper, then upload chunks to MongoDB.

---

## Phase 5: Fix Game Version + Entity Validation + Anti-Hallucination (Detailed Spec)

These 3 issues were found during live Discord testing on 2026-04-02.

### Issue 7: Game Version Not Passed in @mention Flow

**Root cause:** `main.py` line 208 hardcodes `"ngs"`:
```python
extra = await self._retrieve_wiki_context(content, "ngs", "PSO2: New Genesis")
```

The `/ask` slash command correctly uses `GameVersion` enum, but @mention has no way to specify game.

#### Fix: Add `game_version` to RouterAgent output

**File: `core/agents/router_agent.py`**

Add `game_version` to the schema:
```python
class IntentResult(BaseModel):
    intent: str
    confidence: float
    reasoning: str
    game_version: str = "ngs"  # "ngs" or "pso2"
```

Update router system prompt (add after existing rules):
```
Additionally, determine the game version:
- 'ngs': Default. Questions about PSO2: New Genesis.
- 'pso2': Questions about PSO2 Classic / base game, or classes/entities that ONLY exist in PSO2 Classic.

ENTITY-GAME MAP (use this to classify):
- PSO2-ONLY classes (do NOT exist in NGS): Phantom, Hero, Etoile, Luster, Summoner.
- NGS-ONLY classes (do NOT exist in PSO2 Classic): Slayer, Waker.
- Both games: Hunter, Fighter, Ranger, Gunner, Force, Techter, Braver, Bouncer.
- If unsure, default to 'ngs'.
```

**File: `main.py`, line 207-211 (on_message wiki_search handler)**

Replace:
```python
elif result.intent == "wiki_search":
    extra = await self._retrieve_wiki_context(content, "ngs", "PSO2: New Genesis")
```

With:
```python
elif result.intent == "wiki_search":
    game_key = result.game_version  # from router classification
    game_label = "PSO2 Classic" if game_key == "pso2" else "PSO2: New Genesis"
    extra = await self._retrieve_wiki_context(content, game_key, game_label)
```

---

### Issue 8: No Entity-Game Validation

**Root cause:** When user asks `/ask game:NGS question:Phantom class skills`, the slug resolver tries to find `Portal:New_Genesis/Phantom`. This page doesn't exist. MediaWiki API returns an error. `search_wiki()` returns `None`. Bot falls through to model knowledge and hallucinates.

#### Fix: Add validation map to `mcp_client.py`

**File: `core/mcp/mcp_client.py`**

Add after `HEADERS` constant:
```python
# Entity-Game validation maps
PSO2_ONLY_CLASSES = {"phantom", "hero", "etoile", "luster", "summoner"}
NGS_ONLY_CLASSES = {"slayer", "waker"}
BOTH_GAME_CLASSES = {"hunter", "fighter", "ranger", "gunner", "force", "techter", "braver", "bouncer"}

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
                    f"Searching PSO2 Classic data instead."
                )
    elif game_version == "pso2":
        for cls in NGS_ONLY_CLASSES:
            if cls in query_lower:
                return "ngs", (
                    f"**Note:** {cls.title()} is an NGS-only class and does not exist in PSO2 Classic. "
                    f"Searching NGS data instead."
                )
    
    return game_version, None
```

**File: `main.py`, update `_retrieve_wiki_context()`**

Add validation at the top of the method:
```python
async def _retrieve_wiki_context(self, query: str, game_version: str, game_label: str) -> str:
    """Retrieve wiki context via RAG → MediaWiki API → model knowledge fallback chain."""
    # Entity-game validation (e.g., Phantom asked in NGS → redirect to PSO2)
    correction_warning = ""
    from core.mcp.mcp_client import validate_entity_game
    corrected_version, warning = validate_entity_game(query, game_version)
    if warning:
        game_version = corrected_version
        game_label = "PSO2 Classic" if game_version == "pso2" else "PSO2: New Genesis"
        correction_warning = warning + "\n\n"

    # 1. Try RAG ...
    # 2. Fallback: Live wiki ...
    # 3. Final fallback ...
    
    result = ... # existing fallback chain result
    return correction_warning + result
```

Also update the `_WIKI_SLUG_SYSTEM_PROMPT` to include PSO2 Classic page patterns:
```
- PSO2 Classic classes: Hero, Phantom, Etoile, Luster, Summoner (use just "PageName", e.g. "Phantom")
- PSO2 Classic weapons: Katanas_List, Swords_List, etc. (no Portal: prefix)
```

---

### Issue 9: Hallucination When Wiki Fetch Fails

**Root cause:** `build_no_rag_context()` line 60-65 says "Answer from general model knowledge" — Groq/Llama doesn't know PSO2 specifics and invents plausible-sounding but completely false data.

**Evidence from screenshots:**
- Bot invented "Phantom Mark" (not a real skill), "Illusion" (not a real skill), "Shadow Step" (not a real skill)
- Listed fake stats: "Damage Dealt: 120% (Level 1)", "Range: 10m", "Duration: 10s"
- For Slayer, listed PSO2 Base Hunter skills: "Katana Combat", "Wired Lance Combat", "Partisan Combat"

#### Fix A: Harden `build_no_rag_context()` in `main.py`

Replace:
```python
@staticmethod
def build_no_rag_context(game_label: str) -> str:
    return (
        f"[System Context] User is asking about {game_label}. "
        "Answer from general model knowledge only, make uncertainty explicit, "
        "and do not claim wiki-backed accuracy."
    )
```

With:
```python
@staticmethod
def build_no_rag_context(game_label: str) -> str:
    return (
        f"[System Context] User is asking about {game_label}. "
        "CRITICAL: No wiki data was found for this query. "
        "You MUST NOT fabricate or guess any game data including skill names, "
        "stat values, damage numbers, or game mechanics. "
        "Respond with: 'I could not find verified data for this topic in the "
        "ARKS database. Please try a more specific query, or check the wiki "
        "directly at https://pso2na.arks-visiphone.com/wiki/' "
        "If you are confident the entity does not exist in the specified game "
        "version, state that clearly."
    )
```

#### Fix B: Strengthen anti-hallucination in `chat_agent.py`

**File: `core/agents/chat_agent.py`, line 74-82**

Replace the `extra_context` injection block:
```python
prompt += (
    "--- Relevant Retrieved Data ---\n"
    "IMPORTANT: The following context is extracted from the Wiki. "
    "Base your answer ONLY on this context and the specified Game Version. "
    "Do NOT mix mechanics between PSO2 Classic and NGS (e.g., NGS Rangers cannot use Technics).\n"
    "Do NOT fabricate skill names, stat values, or mechanics that are not in the retrieved data.\n"
    "If the retrieved data does not contain the answer, say 'Data not found in the ARKS database.'\n"
    "If sources are present, cite at least one source URL in your final answer.\n"
    f"{evidence_warning}"
    f"{extra_context}\n\n"
)
```

---

### Issue 10: Old Hallucinated Responses Contaminate Memory

**Root cause:** MongoDB `recent_messages` stores the last 30 messages per user, including prior hallucinated assistant responses. These get injected into the Groq prompt as conversation history. The LLM sees its own old wrong answers.

**Evidence from Groq request payload in logs:**
```json
{"role": "assistant", "content": "**Slayer Skills (PSO2)**\n* Katana Combat\n+ Wired Lance Combat..."}
```
This is a previous hallucinated response — still in memory.

#### Fix A: Add `/clear_memory` slash command

**File: `main.py`**

```python
@bot.tree.command(name="clear_memory", description="Clear your conversation history with the bot")
async def clear_memory(interaction: discord.Interaction):
    """Clear the user's conversation history."""
    COMMAND_REQUESTS.labels(command_name="clear_memory").inc()
    session_id = str(interaction.user.id)
    try:
        await bot.memory.clear_memory(session_id)
        await interaction.response.send_message("Memory cleared. Starting fresh.", ephemeral=True)
    except Exception as e:
        print(f"[ERROR] clear_memory failed: {e}")
        await interaction.response.send_message("Failed to clear memory.", ephemeral=True)
```

**File: `core/memory.py`** — add clear method:

```python
async def clear_memory(self, channel_id: str):
    """Delete all memory for a channel/user."""
    await self.collection.delete_one({"channel_id": channel_id})
```

#### Fix B: (Optional) Detect and filter stale hallucinated messages

In `chat_agent.py`, when building the prompt, filter out previous assistant messages that contain known hallucination markers:

```python
# In _build_system_prompt, after appending recent_history:
HALLUCINATION_MARKERS = [
    "Data may be incomplete due to limited information",
    "I recommend checking the official PSO2 website",
]

clean_history = []
for msg in recent_history:
    if msg["role"] == "assistant" and any(m in msg.get("content", "") for m in HALLUCINATION_MARKERS):
        continue  # Skip likely-hallucinated responses
    clean_history.append(msg)
```

---

### Issue 11: `--debug` Floods Terminal with pymongo Noise

**Root cause:** `main.py` line 37 — `logging.basicConfig(level=logging.DEBUG)` sets the ROOT logger to DEBUG. This propagates to pymongo, httpcore, httpx, groq, and every other library.

pymongo sends heartbeat logs every ~10 seconds per shard (3 shards = 6+ log lines every 10 seconds).

#### Fix: Suppress noisy loggers

**File: `main.py`, replace lines 34-39:**

```python
# --debug flag overrides env-based settings
if cli_args.debug:
    import logging
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    app_settings.BOT_DEBUG_ENABLED = True
    app_settings.BOT_DEBUG_INCLUDE_IN_REPLY = True

    # Suppress noisy library loggers even in debug mode
    for noisy_logger in ["pymongo", "httpcore", "httpx", "groq", "urllib3", "asyncio"]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
```

This keeps the bot's own debug logs (MCP, memory, router) visible while silencing library noise.

---

### Issue 13: Follow-up Queries Fail — No Query Expansion

**Root cause:** `main.py` line 237 sends raw user message to `_retrieve_wiki_context`:
```python
extra = await self._retrieve_wiki_context(content, game_key, game_label)
```

When user says "can you more details about it? I need the number for each stage level", the word "it" has no meaning to a text search. The query gets no relevant results, and falls through to "Data not found."

The memory system stores conversation history in MongoDB and passes it to `ChatAgent` for LLM generation, but it is NOT used for RAG retrieval query construction.

#### Fix: Add query expansion step before RAG

**File: `main.py`** — add a method to `PSO2Bot`:

```python
async def _expand_query(self, session_id: str, raw_query: str) -> str:
    """Resolve pronouns and references in follow-up queries using recent conversation."""
    # Get last 4 messages for context
    recent = await self.memory.get_recent_messages(session_id, limit=4)
    if not recent or len(recent) < 2:
        return raw_query  # No conversation history — use as-is

    # Build mini-context for the LLM to resolve references
    history_lines = []
    for msg in recent[-4:]:
        role = "User" if msg["role"] == "user" else "Bot"
        # Truncate long bot replies
        text = msg["content"][:200] + "..." if len(msg["content"]) > 200 else msg["content"]
        history_lines.append(f"{role}: {text}")

    history_block = "\n".join(history_lines)

    prompt = (
        "Given the following conversation history and a new user message, "
        "rewrite the user message as a standalone search query that resolves "
        "all pronouns and references. Output ONLY the rewritten query, nothing else.\n\n"
        f"Conversation:\n{history_block}\n\n"
        f"New message: {raw_query}\n\n"
        "Rewritten query:"
    )

    from google import genai
    from settings import env as config
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    response = await client.aio.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config={"temperature": 0.0, "max_output_tokens": 100},
    )
    expanded = response.text.strip()
    if expanded and len(expanded) > 5:
        print(f"[QUERY_EXPAND] '{raw_query}' → '{expanded}'")
        return expanded
    return raw_query
```

**File: `main.py`, line 237** — call expansion before retrieval:

```python
elif result.intent == "wiki_search":
    game_key = result.game_version
    game_label = "PSO2 Classic" if game_key == "pso2" else "PSO2: New Genesis"
    # Expand vague follow-up queries using conversation context
    search_query = await self._expand_query(session_id, content)
    extra = await self._retrieve_wiki_context(search_query, game_key, game_label)
    reply = await self.chat_agent.generate_reply(session_id, content, extra_context=extra)
```

---

### Phase 5 Task Checklist (updated)

- [ ] Add `game_version: str = "ngs"` field to `IntentResult` in `router_agent.py`
- [ ] Update router system prompt with entity-game classification rules
- [ ] Use `result.game_version` in `on_message` wiki_search handler (replace hardcoded `"ngs"`)
- [ ] Add `validate_entity_game()` function to `mcp_client.py`
- [ ] Call validation in `_retrieve_wiki_context()` before wiki fetch
- [ ] Harden `build_no_rag_context()` — hard refusal, no "answer from model knowledge"
- [ ] Strengthen anti-hallucination in `chat_agent.py` system prompt
- [ ] Extend `_WIKI_SLUG_SYSTEM_PROMPT` with PSO2 Classic page examples
- [ ] Add `/clear_memory` slash command to `main.py`
- [ ] Add `clear_memory()` method to `core/memory.py`
- [ ] Fix `--debug` logging — suppress pymongo/httpcore/groq noise
- [ ] Add `_expand_query()` method for follow-up query resolution
- [ ] Use expanded query in `wiki_search` handler before RAG retrieval
- [ ] Test: `/ask game:NGS question:Phantom class` → should say "Phantom is PSO2 Classic only"
- [ ] Test: `/ask game:NGS question:Slayer skills` → should return real Slayer skills from wiki
- [ ] Test: `/clear_memory` → clears old context, next query gets fresh response
- [ ] Test: `--debug` flag → only bot logs visible, no pymongo heartbeats
- [ ] Test: Follow-up "tell me more about it" → expands to specific query, RAG finds data
- [ ] Test: ask about non-existent entity → should refuse, not hallucinate

---

After all changes:

1. **Build test:** `uv sync` completes without errors
2. **Wiki access test:** `uv run python tests/test_wiki_access.py` passes
3. **Bot startup:** `uv run python main.py` starts without errors
4. **Wiki query:** Mention bot with `@bot What skills does Slayer have?` → gets wiki-sourced answer
5. **Slash command:** `/ask game:NGS question:How does enhancement work?` → wiki content
6. **Fashion test:** `/fashion game:NGS image:<upload>` → structural tags analysis (not error)
7. **Error handling:** Trigger an error → user sees generic message, not stack trace
8. **Docker build:** `docker build .` completes successfully
9. **Scraper test:** `uv run python data/scrapers/wiki_scraper.py ngs:classes` → generates per-skill chunks
10. **Chunk quality:** Inspect `data/storage/wiki_raw/NGS/class/Portal_New_Genesis_Slayer.chunks.json` → should have ~25 chunks, 1 per skill
11. **Game version:** `/ask game:NGS question:Phantom class` → redirects to PSO2 Classic, no hallucination
12. **Anti-hallucination:** Ask about non-existent entity → "Data not found" response, no fabricated data
13. **@mention game detection:** `@bot What skills does Phantom have?` → router detects PSO2 Classic, correct data
