# Development Roadmap

Building a complex Multi-agent system needs to be broken down into Phases for easy acceptance and testing, avoiding the "Built everything but nothing works" scenario.

## Phase 1: Foundation & The Brain (Servers & AI Core) - [1 Week]
**Goal**: Have a bot successfully connect to Discord and communicate with Gemini/Groq APIs.

- [x] Set up GitHub repository and Python directory structure.
- [x] Initialize basic `discord.py` client, connect Bot token successfully.
- [x] Write small Wrappers connecting to Google AI Studio (Gemini) and Groq API.
- [x] Implement test command `/ping_ai` to check the Latency of both models.

## Phase 2: The Knowledge (RAG Treasure Trove) - [2 Weeks]
**Goal**: The bot must answer basic Game information accurately instead of making things up.

- [x] Write raw Scraper to collect Class and Weapon data for NGS from Arks-Visiphone.
- [x] Set up Pinecone Database, convert scraped data into Vectors.
- [x] Write simple RAG Pipeline: Semantic Search -> Return Context -> LLM synthesis.
- [x] Test Anti-Hallucination rules.

## Phase 3: The Soul (Persona System & Multilingual) - [1 Week]
**Goal**: The bot starts having a "soul", communicating smoothly like a real character in Vietnamese.

- [ ] Write translation pipeline (Cross-lingual Flow): Prompt EN -> Search EN -> Translate VN.
- [ ] Create Prompt files for at least 2 sample characters (e.g., Matoi, Xiera).
- [ ] Integrate Short-term Context Memory feature.

## Phase 4: The Eyes (Reverse Look-up Phashion) - [1.5 Weeks]
**Goal**: Deploy the blockbuster Image-based Fashion Recognition.

- [ ] Write Scraper to get image and description data from specialized Phashion pages.
- [ ] Utilize Gemini 1.5 Flash's Vision feature to extract keywords from user-sent images.
- [ ] Build a Match algorithm between Gemini's description and the Database.

## Phase 5: Optimization & Deployment (Zero-Cost Liftoff) - [1 Week]
**Goal**: Optimize finances, deploy to Cloud, and hand over.

- [ ] Integrate Token saving algorithms (Fast Bypass, Intent Router).
- [ ] Register Oracle/GCP Cloud and deploy code.
- [ ] Setup Prometheus & Grafana to create monitoring dashboards.
- [ ] Light Load test and monitor VPS RAM usage.
