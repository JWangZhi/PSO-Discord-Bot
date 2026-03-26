# PSO2 / NGS Wiki Scraper Strategy V3.0 — FINAL LOCKED

**Goal:** Build a robust, 3-layer pipeline leveraging the **MediaWiki API** to scrape the [Arks-Visiphone Global Wiki](https://pso2na.arks-visiphone.com) and extract clean, structured data for the PSO2/NGS Discord Bot's RAG system.

**Status:** Debated & Locked (2025-03-23)

---

## 1. Context & Scope

The wiki is divided into two main portals:
- **New Genesis (NGS):** Pages use the prefix `Portal:New_Genesis/` (e.g., `.../wiki/Portal:New_Genesis/Hunter`).
- **Base PSO2 (Classic):** Pages use flat paths with **no** prefix (e.g., `.../wiki/Hunter`).

**API Endpoint:** `https://pso2na.arks-visiphone.com/api.php`

> [!WARNING]
> **Redlinks:** Pages that do not exist are detected via `prop=info` → `missing` flag. Never attempt to scrape these.

---

## 2. Architecture: 3-Layer Pipeline

### Layer 1: Manifest (`wiki_scraper_manifest.json`)

A **manually curated** JSON file — the single source of truth for all scraping targets. No auto-discovery writes to this file.

- **`game_mode`**: `"ngs"` or `"pso2"`
- **`category`**: Semantic label (`"class"`, `"weapon"`, `"system"`, `"world"`, `"progression"`)
- **`scrape_type`**:
  - `"structured_table"` — Primarily data tables (weapon/armor lists)
  - `"text_document"` — Primarily descriptive text (guides, system info)
  - `"mixed"` — Both tables and text (class pages, skill pages)

**Why manual manifest over auto-discovery:**
1. `categorymembers` cannot infer `scrape_type` (only humans decide)
2. Wiki categories are inconsistent between NGS and PSO2
3. Orphan pages may not belong to any category
4. **MVP execution priority order cannot be auto-inferred** — manifest controls scrape ordering

**Supplementary:** A `manifest_discovery.py` audit tool uses `categorymembers` API to **suggest** new pages not yet in the manifest. Human reviews and adds manually.

---

### Layer 2: Scraper Execution (`wiki_scraper.py`)

#### Step 1: Batch Validation + Incremental Check (SINGLE API Call)

Combine existence check and revision timestamp in one batched request (50 titles/batch, anonymous API limit):

```
GET api.php?action=query&prop=info|revisions&rvprop=timestamp&titles=Page1|Page2|...|Page50&format=json
```

For each page in the response:
- **`missing` key present** → Redlink, skip entirely
- **`revisions[0].timestamp` ≤ `scraped_at` from `.meta.json`** → No changes, skip
- **Otherwise** → Proceed to fetch

> [!TIP]
> This replaces both the old HEAD request and the separate incremental check, cutting API calls by ~50%.

Optional `--fast` flag: skip this step entirely and always fetch (useful for first run or forced refresh).

#### Step 2: Content Fetch via MediaWiki API

```
GET api.php?action=parse&page={page_path}&prop=text&format=json
```

- Returns **rendered HTML** (templates expanded, Lua resolved) inside a JSON wrapper.
- Feed `response["parse"]["text"]["*"]` directly into BS4 pipeline — **zero refactor** from existing parsers.
- **Local Cache:** Save raw HTML to `data/storage/cache/{safe_name}.html` with **24h TTL** to avoid redundant API calls during dev/tuning.

> [!IMPORTANT]
> **Fallback Plan:** If Arks-Visiphone starts rate-limiting `action=parse` aggressively at scale (100+ pages), switch to `action=query&prop=revisions&rvprop=content` + `mwparserfromhell` for table extraction only. Keep `action=parse` for text chunk extraction.

#### Step 3: Extract Tables (For `structured_table` & `mixed`)

- Use **`pandas.read_html(html_string)`** on the HTML from `action=parse`. Pandas automatically flattens `rowspan` and `colspan`.
- **Mid-table Headers:** Post-process filter rows where all column values are identical (sub-header rows injected mid-table).
- Convert to `list[dict]` via `df.to_dict("records")`.
- **Enrich** each row with: `_game_mode`, `_category`, `_page`, `_source_url`.

#### Step 4: Extract Text Chunks (For `text_document` & `mixed`)

- **Preamble Capture:** Pre-initialize section labels to capture intro text before the first `<h2>`:
  ```python
  current_h2 = "Introduction"
  current_h3 = "Overview"
  ```
- Split content on `<h2>` / `<h3>` boundaries. Convert HTML buffer to Markdown via `markdownify`.
- Ignore chunks shorter than 30 characters.
- **Deterministic Chunk ID** for Pinecone upsert (prevents duplicates on re-scrape):
  ```python
  chunk_id = md5(f"{url}::{section}::{sub_section}".encode()).hexdigest()
  ```

#### Step 5: Save Files

Output: `data/storage/wiki_raw/{GAME_MODE}/{category}/`

| File | Contents |
|---|---|
| `{safe_name}.tables.json` | Structured table data (list of table dicts) |
| `{safe_name}.chunks.json` | Semantic text chunks with `chunk_id` |
| `{safe_name}.md` | Raw Markdown backup (debug/audit) |
| `{safe_name}.meta.json` | `page_path`, `url`, `game_mode`, `category`, `scrape_type`, `n_tables`, `n_chunks`, `scraped_at`, **`lastrevid`** |

Safe filename: `re.sub(r"[^\w\-]", "_", page_path)`

#### Step 6: Rate Limiting

- **1.2s sleep** between API requests (bypassed when hitting local cache).
- Respect MediaWiki `maxlag` parameter if server is under load.

---

### Layer 3: Output Structure

```text
data/storage/
├── cache/                              # 24h TTL raw HTML cache
│   └── Portal_New_Genesis_Hunter.html
└── wiki_raw/
    ├── NGS/
    │   ├── class/
    │   │   ├── Portal_New_Genesis_Hunter.md
    │   │   ├── Portal_New_Genesis_Hunter.chunks.json
    │   │   ├── Portal_New_Genesis_Hunter.tables.json
    │   │   └── Portal_New_Genesis_Hunter.meta.json
    │   ├── weapon/
    │   ├── system/
    │   └── world/
    └── PSO2/
        ├── class/
        ├── weapon/
        └── armor/
```

---

## 3. Manifest Structure

```json
{
  "ngs": {
    "classes": {
      "scrape_type": "mixed",
      "category": "class",
      "pages": [
        "Portal:New_Genesis/Hunter",
        "Portal:New_Genesis/Fighter"
      ]
    }
  },
  "pso2": {
    "weapons_by_type": {
      "scrape_type": "structured_table",
      "category": "weapon",
      "pages": ["Swords_List", "Wired_Lances_List"]
    }
  }
}
```

> [!CAUTION]
> **Confirmed Redlinks — Never Add to Manifest:**
> - **PSO2:** Consumables, Discs, Materials, Other, A.I.S., Ridroid, Stickers, Beauty_Salon, Weather, PSE, Partners, Loading_Tips, Settings, Friend_Partners, Friend_Invitation, Fashion_Catalog, Items(hub), Item_Lab(hub), ARKS_League, Bingo_Cards
> - **NGS:** `Portal:New_Genesis/Special_Equipment`, `Portal:New_Genesis/EX_Style`

---

## 4. Runner Interface & Batching

```bash
uv run python wiki_scraper.py                # Scrape all (with incremental check)
uv run python wiki_scraper.py --fast          # Scrape all, skip validation/incremental
uv run python wiki_scraper.py ngs             # NGS only
uv run python wiki_scraper.py pso2            # PSO2 only
uv run python wiki_scraper.py classes         # Section "classes" across both games
uv run python wiki_scraper.py ngs:classes     # NGS classes only
uv run python wiki_scraper.py ngs:weapons     # NGS weapons only
```

### MVP Execution Priority Order

| Priority | Batch | Description |
|---|---|---|
| 1 | `ngs:classes` | Core combat classes |
| 2 | `ngs:class_overview` | Class system overview |
| 3 | `ngs:weapons` | Weapon series tables |
| 4 | `pso2:classes` | Classic class pages |
| 5 | `ngs:combat_systems` | PA, Techniques, Augments, Item Lab |
| 6 | `pso2:weapons_by_type` | Classic weapon lists |
| 7 | `ngs:world` | Quests, Exploration |
| 8 | `ngs:progression` | Titles, Mission Pass |
| 9 | `pso2` | All remaining PSO2 content |

---

## 5. Downstream Integration (RAG)

- **`.tables.json`** → Exact-match query system (ATK values, drop locations, stat comparisons)
- **`.chunks.json`** → Embed via Local Embedding → Pinecone vector DB for semantic search
- **Filter:** Always use `_game_mode` metadata to scope results to the correct game version
- **Citations:** Bot responses must include the source `url` from the chunk/row
- **Chunk ID:** Each chunk carries a deterministic `chunk_id` → Pinecone upsert overwrites stale vectors automatically

> [!WARNING]
> **Pinecone Metadata Limit:** Values capped at **512 bytes**. Truncate long Unicode item names/descriptions before `upsert`.

---

## 6. Dependencies

```
requests
beautifulsoup4
markdownify
pandas
lxml
```
