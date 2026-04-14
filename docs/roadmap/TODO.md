# Project Progress (TODO List)

*This progress tracker is designed based on the Development Roadmap so the User can easily track it directly in the root directory.*

## Phase 1: Foundation (Core Initialization)
- [x] Architecture Design and Documentation (`docs/`)
- [x] Finalize RP Memory and Zero-Cost Strategy
- [x] Initialize project using `uv`
- [x] Setup Directory Structure (core, bot, data, ai_prompts...)
- [x] Install core packages (`discord.py`, `groq`, `google-generativeai`, `motor`)
- [x] Configure `.env` template (`.env.example`)
- [x] Write basic bot script (`main.py`) and run `/ping` command to test Discord connection.

## Phase 2: Knowledge & Scraper (The Knowledge)
- [x] Create `rag_pipeline.py` skeleton.
- [x] Write Scraper to download Class/Weapon data from Visiphone Wiki.
- [x] Upload wiki data to MongoDB and create text indexes.
- [x] Complete Search flow (Data Researcher Agent).

## Phase 3: Roleplay & Emotions (The Soul)
- [x] Setup Connection to MongoDB.
- [x] Write module to manage Long-term & Short-term Memory.
- [x] Write automatic Summarization mechanism (Context Compression).
- [x] Setup sample Persona files in `ai_prompts/characters/`.

## Phase 4: Fashion Recognition (The Eyes)
- [x] Integrate Gemini Vision.
- [x] Image noise reduction & Search keyword analysis.
- [x] Cross-check with Phashion database.

## Phase 5: Optimization & Expansion (Deployment)
- [ ] Complete Token Optimization (Fast Route, Intent Matrix).
- [ ] Write `Dockerfile` and test build using `uv`.
- [ ] Setup Grafana + Prometheus exporter in code.
- [ ] Deploy to Oracle Cloud / GCP.
