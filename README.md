# PSO2 Smart Companion Bot

Welcome to the ultimate AI-powered Discord bot designed exclusively for the **Phantasy Star Online 2 (PSO2: NGS)** community! 

If you've ever felt overwhelmed by the countless weapons, skill trees, or fashion items in the game, this bot is here to be your ultimate guide and companion.

---

## What does this Bot actually do?

Imagine taking the entire PSO2 Wiki, giving it a brain, giving it eyes, and wrapping it in the personality of your favorite in-game characters. That is exactly what this bot is.

It has three main superpowers:

### 1. The Game Expert (Smart Q&A)
You don't need to manually search through long Wiki pages anymore. You can just ask the bot naturally: *"How should I build my Slayer class?"* or *"What does the Fear Eraser skill do?"*
The bot will instantly read through its vast, stored knowledge of the PSO2 Wiki, find the exact answer, and explain it to you simply.

### 2. The Fashion Detective (Reverse Image Look-up)
*End-game is Fashion.* You see a beautiful outfit screenshot on Discord or X (Twitter), but the poster didn't list the items they used.
Just reply to the image with a command! The bot will **look at the picture** using AI vision, analyze the hairstyle, accessories, and colors, and then match it against its database to tell you exactly which cosmetic items you are looking at.

### 3. The Roleplay Companion (Persona System)
You aren't talking to a boring, robotic machine. You can configure the bot to act like beloved characters from the game!
If you choose **Matoi**, she will call you "Guardian" and speak to you sweetly and loyally. If you choose **Xiera**, she will be energetic, slightly sassy, and talk about analyzing data. The bot even remembers what you talked about!

---

## How does it work? (The non-technical explanation)

To make everything fast, accurate, and completely free to operate, the bot works like a highly organized team of specialists. Here is the step-by-step workflow:

### Step 1: Building the Encyclopedia
While you are sleeping, the bot is working. Once a week, it quietly goes to the official community Wiki (Arks-Visiphone) and reads everything. It turns every weapon stat, every skill description, and every fashion item into a "digital fingerprint" (Vectors) and stores it in its own special vault (Pinecone Database).

### Step 2: The "Traffic Cop"
When you send a message to the bot, it doesn't just blindly fire up its biggest, most expensive brain. Instead, a super-fast, tiny AI looks at your message like a Traffic Cop and says:
- *"Oh, they just said 'Hello'! Send them to the Fast Chat desk."* -> Gets an instant, friendly reply.
- *"Ah, they are asking a hard question about damage multipliers!"* -> Sends the request to the **Researcher Agent**, who runs to the library vault to find the answer.
- *"Wait, this is a picture!"* -> Sends the image to the **Vision Agent** (Gemini) to analyze what the outfit looks like.

### Step 3: The Language Bridge (Auto-Translation)
What if you ask a question in Vietnamese, but the Wiki is in English? No problem.
1. You ask: *"Vũ khí mạnh nhất cho Hunter là gì?"*
2. The bot secretly translates it to English inside its head.
3. It searches the English library vault.
4. It finds the answer, comprehends it, and then translates the final answer back into beautiful, natural Vietnamese for you, keeping the exact English names for items so you don't get confused.

### Step 4: The Immortal Memory (Context Compression)
Usually, if you chat with an AI for 2 hours, it "forgets" what you said earlier, or the server bill becomes thousands of dollars because reading a massive chat history is expensive.
Our bot uses a brilliant trick called **Context Compression**. 
Every time you exchange 10 messages with the bot, a tiny librarian AI secretly looks at the conversation and writes a 1-sentence summary (e.g., *"The player is stuck in a dungeon and Matoi is worried about them."*). 
It saves this tiny summary in its long-term memory vault (MongoDB) and tosses the old messages. This way, the bot perfectly remembers who you are and what is happening, but its memory stays permanently lightweight and blazing fast!

---

## Why this design is special

By splitting the brain into many pieces (a "Multi-agent" system), we achieve something incredible: **Zero-cost operation.** 

Instead of paying a massive monthly fee to run one giant AI, we use clever coding to route tasks directly to the best *Free Tier* services available worldwide. We use local databases, intelligent text chunking, and memory compression to ensure the bot can serve thousands of PSO2 players 24/7 without ever crashing or charging a dime. 

*Prepare to elevate your ARKS adventure!*

---

## Configuration Layout

The project now uses a centralized settings structure:

- `settings/env.py`: secrets and environment-backed runtime values
	- API keys, DB URIs, model endpoints, and feature toggles such as `DISABLE_RAG`.
- `settings/app.py`: app-level operational defaults
	- ports, index names, thresholds, and batch sizes.
- `settings/scraper.py`: wiki scraper-specific defaults
	- wiki endpoint, cache/TTL, request delay, chunking and table heuristics.

Legacy modules remain as compatibility shims:

- `config.py` -> re-exports `settings/env.py`
- `app_settings.py` -> re-exports `settings/app.py`
- `data/scrapers/scraper_settings.py` -> re-exports `settings/scraper.py`

Use `.env.example` as the source template for environment overrides.
Detailed guide: `docs/13-config-structure.md`.
