# PSO2 Smart Companion Bot

An AI-powered Discord bot for the **Phantasy Star Online 2 (PSO2: NGS)** community — game expert, fashion detective, and roleplay companion in one.

---

## Features

### 1. Game Expert (Smart Q&A)
Ask naturally: *"How should I build my Slayer class?"* or *"What does Fear Eraser do?"*
The bot searches its local wiki database (Arks-Visiphone) and replies with sourced, accurate data. No hallucination guardrails built in.

### 2. Fashion Detective (Reverse Image Lookup)
Upload a screenshot and the Vision Agent (Gemini) analyzes the outfit — hairstyle, accessories, color — and identifies the cosmetic items.

### 3. Roleplay Companion (RP Channel Mode)
Admins can designate any channel as an **RP channel** with `/rp_enable`. In RP mode:
- Bot responds to **every message** (no @mention needed)
- Uses **channel-level shared memory** — all users share the same conversation context
- Remembers conversation history with automatic context compression
- `/rp_disable` to turn off; `/clear_memory` to reset the channel's memory

Available personas: `default` (ARKS System Advisor), `matoi`, `xiera` — configured per guild in `ai_prompts/characters/`.

---

## Architecture

```
on_message
    ├── is_rp_channel? ──yes──▶ ChatAgent (session = channel_id)
    │                                └── MemoryManager (shared channel memory)
    └── @mention? ──yes──▶ RouterAgent (Gemini Flash)
                                ├── "chat"         ──▶ ChatAgent (session = user_id)
                                ├── "wiki_search"  ──▶ WikiSearchService → ChatAgent
                                └── "fashion_match"──▶ VisionAgent → ChatAgent
```

**Memory system (2-layer):**
- `rp_memory` — rolling window (working memory for LLM context)
- `rp_history` — append-only raw log per channel, TTL 30 days
- `ContextCompressor` — auto-summarizes when buffer exceeds threshold, keeps context lightweight

---

## Slash Commands

| Command | Description | Permission |
|---|---|---|
| `/ask` | Ask a wiki question by game version | Everyone |
| `/fashion` | Identify outfit from image | Everyone |
| `/wiki` | Fetch & summarize a wiki URL | Everyone |
| `/rp_enable` | Enable RP mode in current channel | Manage Channels |
| `/rp_disable` | Disable RP mode in current channel | Manage Channels |
| `/clear_memory` | Reset conversation memory | Everyone |
| `/ping` | Check bot latency | Everyone |

---

## Project Layout

```
PSO_bot/
├── main.py                  # Bot entry point, event handlers, slash commands
├── core/
│   ├── agents/
│   │   ├── chat_agent.py    # Groq/LLaMA — generates replies
│   │   ├── router_agent.py  # Gemini Flash — classifies intent
│   │   └── vision_agent.py  # Gemini — analyzes images
│   ├── db.py                # MongoDB singleton, collection names, index definitions
│   ├── memory.py            # MemoryManager — rp_memory + rp_history CRUD
│   ├── context_compressor.py# Auto-summarizes old messages into long-term memory
│   ├── wiki_search.py       # Hybrid MongoDB wiki search
│   └── mcp/                 # Optional: live MediaWiki API bridge
├── bot/
│   └── cogs/
│       └── rp_chat.py       # RPChannelManager — RP channel registry + cache
├── ai_prompts/
│   └── characters/          # Persona system prompts (default, matoi, xiera)
├── scripts/
│   ├── scraper/             # wiki_scraper.py — Arks-Visiphone scraper
│   ├── etl/                 # upload_wiki_to_mongo.py — loads scraped data to DB
│   └── rag/                 # rag_pipeline.py, reembed_from_cache.py
├── settings/
│   ├── env.py               # Secrets & runtime values (API keys, URIs)
│   ├── app.py               # Operational defaults (ports, thresholds, limits)
│   └── scraper.py           # Scraper-specific config
├── data/storage/            # Cached HTML from wiki scraper
└── docs/                    # Architecture docs, roadmap, guides
```

---

## Setup

```bash
# 1. Install dependencies
uv sync

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 3. Run the bot
python main.py

# Optional: enable live wiki fetching via MCP
python main.py --mcp

# Optional: verbose debug output
python main.py --debug
```

**Required environment variables** (see `.env.example`):
- `DISCORD_BOT_TOKEN`
- `GROQ_API_KEY` + `GROQ_MODEL`
- `GEMINI_API_KEY`
- `MONGODB_URI`

---

## Configuration

- `settings/env.py` — secrets, API keys, model endpoints, feature flags (`DISABLE_RAG`)
- `settings/app.py` — ports, memory buffer size, Discord message limit
- `settings/scraper.py` — wiki endpoint, cache TTL, chunking heuristics

See [docs/13-config-structure.md](docs/13-config-structure.md) for details.
