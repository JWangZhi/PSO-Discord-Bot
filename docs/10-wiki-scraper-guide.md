# Wiki Scraper — Usage Guide

Command: `uv run python data/scrapers/wiki_scraper.py [batch] [flags]`

---

## Batch Targeting

| Command | Description |
|---|---|
| `uv run python data/scrapers/wiki_scraper.py` | Scrape **everything** in manifest |
| `uv run python data/scrapers/wiki_scraper.py ngs` | All NGS sections only |
| `uv run python data/scrapers/wiki_scraper.py pso2` | All PSO2 sections only |
| `uv run python data/scrapers/wiki_scraper.py classes` | Section `classes` across both games |
| `uv run python data/scrapers/wiki_scraper.py ngs:classes` | NGS classes only |
| `uv run python data/scrapers/wiki_scraper.py ngs:weapons` | NGS weapons only |
| `uv run python data/scrapers/wiki_scraper.py pso2:armor` | PSO2 armor only |

**Syntax:** `[game]:[section]` — left side filters by game, right side filters by section name in manifest.

---

## Flags

| Flag | Effect |
|---|---|
| `--fast` | Skip API validation & incremental check. Always fetch and process every page. Useful for first run or forced refresh. |

Example: `uv run python data/scrapers/wiki_scraper.py ngs:classes --fast`

---

## Available Sections (from `wiki_scraper_manifest.json`)

### NGS

| Section | Type | Category | Pages |
|---|---|---|---|
| `class_overview` | mixed | class | 4 |
| `classes` | mixed | class | 10 |
| `ex_styles` | mixed | ex_style | 3 |
| `weapons` | structured_table | weapon | 6 |
| `combat_systems` | mixed | system | 10 |
| `world` | mixed | world | 18 |
| `progression` | mixed | progression | 11 |
| `items` | mixed | item | 4 |
| `scratches` | structured_table | scratch | 5 |
| `shops` | structured_table | shop | 5 |
| `community` | text_document | system | 7 |
| `creative_space` | mixed | creative | 5 |

### PSO2

| Section | Type | Category | Pages |
|---|---|---|---|
| `class_overview` | mixed | class | 2 |
| `classes` | mixed | class | 13 |
| `weapons_by_type` | structured_table | weapon | 19 |
| `weapon_systems` | mixed | system | 6 |
| `armor` | structured_table | armor | 6 |
| `content` | text_document | system | 6 |
| `progression` | mixed | progression | 6 |
| `other_systems` | mixed | system | 6 |
| `fashion` | structured_table | fashion | 12 |
| `shops` | structured_table | shop | 10 |
| `scratches` | structured_table | scratch | 3 |

---

## MVP Priority Order

Run in this order for fastest bot usefulness:

```bash
uv run python data/scrapers/wiki_scraper.py ngs:classes --fast
uv run python data/scrapers/wiki_scraper.py ngs:class_overview --fast
uv run python data/scrapers/wiki_scraper.py ngs:weapons --fast
uv run python data/scrapers/wiki_scraper.py pso2:classes --fast
uv run python data/scrapers/wiki_scraper.py ngs:combat_systems --fast
uv run python data/scrapers/wiki_scraper.py pso2:weapons_by_type --fast
uv run python data/scrapers/wiki_scraper.py ngs:world --fast
uv run python data/scrapers/wiki_scraper.py ngs:progression --fast
uv run python data/scrapers/wiki_scraper.py pso2 --fast
```

---

## Output Files

Each scraped page produces 4 files in `data/storage/wiki_raw/{GAME}/{category}/`:

| File | Purpose |
|---|---|
| `*.tables.json` | Structured table data (weapon stats, skill values) |
| `*.chunks.json` | Semantic text chunks for RAG embedding (with `chunk_id`) |
| `*.md` | Raw Markdown backup for debugging |
| `*.meta.json` | Scrape metadata (`scraped_at`, `lastrevid` for incremental) |

---

## Incremental Updates

Without `--fast`, the scraper automatically:
1. Checks wiki revision timestamps via MediaWiki API
2. Compares with local `scraped_at` in `.meta.json`
3. Skips pages that haven't changed since last scrape

This makes re-runs fast and polite to the wiki server.

---

## Caching

Raw HTML is cached locally in `data/storage/cache/` with a **24-hour TTL**. During development/tuning, the scraper reads from cache instead of hitting the API — allowing rapid iteration without network calls.

To force a fresh fetch, delete the cache files:
```bash
rm -rf data/storage/cache/
```

---

## Additional Source: Official PSO2 Players Site

To ingest official content from `https://pso2.com/players/` into RAG chunks:

```bash
uv run python data/scrapers/players_scraper.py
```

Optional page cap:

```bash
uv run python data/scrapers/players_scraper.py --max-pages 60
```

Then upload all chunks (including players source) to Pinecone:

```bash
uv run python data/rag/embed_uploader.py
```
