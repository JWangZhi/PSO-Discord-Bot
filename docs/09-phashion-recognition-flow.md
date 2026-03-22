# Phashion Recognition Flow (The Eyes)

This document details the workflow for Phase 4: Reverse Fashion Image Look-up. Because Phantasy Star Online 2 (PSO2: NGS) allows extreme player customization (colors, body proportions, accessories), traditional pixel-matching algorithms fail. 

Our solution relies on **Structural Tagging** using Google Gemini 1.5 Flash Vision.

---

## 1. Database Generation Pipeline (Scraping)

Before the bot can answer user queries, it must build a massive "Dictionary of Outfits" (The Pinecone Vector Database). This is done entirely in the background via `data/scrapers/phashion_scraper.py`.

### Step 1: Structure Extraction (Fast & Safe)
Instead of aggressively downloading every image on the internet, the scraper first uses HTTP requests and `BeautifulSoup` to parse clean HTML tables from the Arks-Visiphone Wiki.
- Finds `<table class="wikitable">`
- Extracts **Item Name** (e.g., *N-Combat Dress [Ba]*)
- Extracts **Image URL** (e.g., *https://.../CombatDress.png*)

### Step 2: Intelligent Downloading 
Only when a valid Name and URL are parsed does the scraper send a request to download the raw image bytes.

### Step 3: Auto-Tagging (The Magic)
The raw image bytes are sent to the **Vision Agent** (`core/agents/vision_agent.py`). 
The Gemini model is given a strict, explicit prompt:
- **IGNORE**: Colors, Faces, Hair, Lighting. (Because users change these constantly).
- **FOCUS ON**: Silhouette, Materials (leather, metal), Patterns (stripes, belts, mechanical limbs).
- **OUTPUT**: A strictly formatted JSON array of characteristics (e.g., `["sci-fi uniform", "twin belts", "asymmetrical skirt"]`).

### Step 4: Vector Embedding & Storage
The JSON array of tags is squashed into a text string.
The Local Embedding Model (via LM Studio) converts this string of tags into a mathematical vector (a list of 768 numbers).
This vector, along with the Item Name and original Thumbnail URL, is permanently stored in **Pinecone**.

---

## 2. User Lookup Pipeline (Discord Interaction)

Once the database is built, the Bot is ready for players. This happens in real-time when a user chats with the Bot.

### Step 1: The User Request
A user attaches a screenshot of a cool outfit they saw in the game lobby to Discord and asks the Bot: *"What is this person wearing?"*

### Step 2: The Vision Agent Parsing
The bot downloads the Discord attachment and sends it to the same **Vision Agent**.
Using the exact same strict prompt, the Vision Agent looks at the user's customized, weirdly-colored screenshot. It ignores the green skin and pink hair, and successfully extracts the structural tags:
`{"structural_tags": ["sci-fi uniform", "twin belts", "asymmetrical skirt"]}`

### Step 3: The Vector Search
The extracted tags are sent to the Local Embedding Model and converted into a Vector.
This Query Vector is sent to Pinecone to find the "Nearest Neighbors" (Similarity Search).

### Step 4: The Result
Pinecone returns the top 3 closest matches from the official Arks-Visiphone database based on structural similarity (because the "twin belts" and "sci-fi uniform" vectors align closely).

The Bot creates a beautiful Discord Embed showing:
1. The official Item Name (*N-Combat Dress [Ba]*).
2. The official Wiki Thumbnail (to prove it's the right item).
3. A Match Confidence Score (e.g., 94% structural match).
