# Config Structure Guide

Last updated: 2026-03-27

## Goal
Keep configuration maintainable and avoid hardcoded constants spread across modules.

## Canonical Modules

### 1. `settings/env.py`
Use for environment-backed values and secrets.

Examples:
- `DISCORD_BOT_TOKEN`
- `GEMINI_API_KEY`, `GROQ_API_KEY`
- `PINECONE_API_KEY`, `MONGODB_URI`
- `LOCAL_EMBED_URL`, `LOCAL_EMBED_MODEL`
- `DISABLE_RAG`

Rule:
- Anything sensitive or deployment-specific belongs here.

### 2. `settings/app.py`
Use for app-wide operational defaults.

Examples:
- runtime limits (`DISCORD_MESSAGE_LIMIT`, `METRICS_PORT`)
- routing defaults (`ROUTER_MODEL`, `ROUTER_TEMPERATURE`)
- DB collection/index names (`APP_DB_NAME`, `RAG_WIKI_INDEX_NAME`, `PHASHION_INDEX_NAME`)
- RAG thresholds and batch sizes
- vision runtime defaults

Rule:
- Non-secret values that may require tuning across environments belong here.

### 3. `settings/scraper.py`
Use for wiki scraper-only settings.

Examples:
- `WIKI_API_BASE`, `WIKI_BASE_URL`
- `WIKI_REQUEST_DELAY`, `WIKI_CACHE_TTL_H`
- `WIKI_MIN_CHUNK_LEN`, `WIKI_BATCH_SIZE`
- scraper table heuristics (`NAV_TABLE_CLASS_HINTS`, `NAV_CAPTION_HINTS`)

Rule:
- Values used only by scraper pipelines belong here.

## Compatibility Shims
To avoid breaking existing imports:

- `config.py` re-exports from `settings/env.py`
- `app_settings.py` re-exports from `settings/app.py`
- `data/scrapers/scraper_settings.py` re-exports from `settings/scraper.py`

These shims should remain for compatibility, but new code should import from `settings.*` directly.

## Import Conventions

Preferred imports:

```python
from settings import env as config
from settings import app as app_settings
from settings import scraper as scraper_settings
```

Avoid new direct imports from legacy shim files in new modules.

## .env Management

Use `.env.example` as the contract for supported environment variables.

When adding a new setting:
1. Add default in `settings/env.py`, `settings/app.py`, or `settings/scraper.py`.
2. Add the corresponding entry in `.env.example`.
3. Mention it in module docstring/comments if behavior-critical.

## Practical Checklist

- No new hardcoded operational constants inside business modules.
- Secrets only in env-backed config.
- Scraper-specific knobs stay in `settings/scraper.py`.
- New config keys documented in `.env.example`.
