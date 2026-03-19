# Tech Stack & Project Layout

The project is built using Python, taking advantage of its powerful AI ecosystem and the easiest-to-use Discord library.

## 1. Tech Stack

- **Language**: `Python 3.10+` (Excellent Async/Await support for bots).
- **Package Manager**: `uv` (Ultra-fast package manager written in Rust, completely replacing pip/poetry/virtualenv).
- **Core Framework**: `discord.py` (Latest version supporting Slash Commands & Interactions).
- **AI & RAG Orchestration**: 
  - `LangChain`: Used to connect APIs, manage query Chains, and Memory.
  - Providers: `google-generativeai` (Gemini), `groq` (Llama 3).
- **Database Clients**:
  - `pymongo` (Motor for asynchronous operations) -> Manages NoSQL.
  - `pinecone-client` -> Interacts with the Vector Database.
- **Scraping & Utilities**: `beautifulsoup4`, `requests`, `markdownify`.

## 2. Project Directory Tree (Expected Layout)

Standard Scalable structure, clearly separating the responsibilities of each Module.

```text
pso2_bot/
├── .env                        # Contains API Keys, DB URIs (Absolutely do not commit)
├── config.py                   # Loads environment variables and sets constants
├── main.py                     # Entry point to launch the Bot
│
├── core/                       # 🧠 AI Heart (Multi-Agent)
│   ├── orchestrator.py         # Conductor managing Tasks
│   ├── agents/
│   │   ├── advisor_agent.py    # Game expert logic
│   │   ├── vision_agent.py     # Image analysis logic (Phashion)
│   │   └── persona_agent.py    # Roleplay logic
│   └── optimizations/
│       └── token_manager.py    # Token saving mechanisms
│
├── bot/                        # 🤖 Discord Client
│   ├── client.py               # Setup discord.py Bot object
│   ├── events.py               # Handles on_message, on_ready
│   └── cogs/                   # Command groups (Slash Commands)
│       ├── ask_cmd.py          # Q&A command
│       ├── phashion_cmd.py     # Image search command
│       └── admin_cmd.py        # Sets Persona/Config for Server
│
├── data/                       # 🗄️ Knowledge Management
│   ├── rag_pipeline.py         # Manages Retrieval process
│   ├── scrapers/               # Scripts to scrape Wiki data
│   └── db_clients.py           # MongoDB and Pinecone connections
│
├── ai_prompts/                 # 🎭 System Prompts Management
│   ├── characters/             # Contains config files for Matoi, Xiera...
│   └── core_instructions.md    # Anti-Hallucination rules
│
└── utils/                      # 🛠️ General utilities
    ├── logger.py               # Formats Logs for console/file
    └── metrics.py              # Exports metrics for Prometheus/Grafana
```
