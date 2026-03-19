# Data Strategy & Multilingual Processing Workflow

The quality of AI depends on the quality of data. Therefore, the data ingestion and knowledge processing (Knowledge Base) workflow must be extremely rigorous.

## 1. Data Ingestion Pipeline

The project data is scraped periodically from the Arks-Visiphone Wiki. This is a **completely independent process (Offline/Background Job)** from the Discord Bot itself. This ensures the Bot doesn't slow down or hang while serving players.

### A. AI-Assisted Scraping
There are 2 hybrid methods depending on the Wiki page type:

1. **Rule-based Scraping (No AI)**:
   - Uses Python tools (`BeautifulSoup`) to extract precise data from Weapon/Armor stat tables.
   - **Cost**: Only consumes a tiny bit of server CPU/RAM, runs in seconds. Cost: **$0**.

2. **LLM Parsing (AI Model involved)**:
   - Instead of laboriously filtering HTML garbage page by page, we scrape the entire raw text of the Wiki page and pass it to an AI Model (like Gemini 1.5 Flash) to process.
   - Prompt: *"Take this raw HTML/Text, find all info about the character Matoi and format it into a perfect Markdown article"*.
   - The AI automatically understands, summarizes, and cleans the data to feed into the Database.
   - **How expensive is it?**: Still **$0**. Data scraping/updating usually happens 1 time/week (on PSO2 maintenance day). It will use about 10-50 API requests on that day, sitting comfortably within the Free 1500 req/day tier of Gemini.

### B. Chunking & Embedding
- The cleaned Markdown data is "chunked" into short segments of about 500-1000 words.
- These chunks are fed into an Embedding model (turning text into a Vector array) and saved into the **Pinecone Vector Database**.

## 2. Building the Vector Database

- The cleaned data is converted to numerical vectors (Embeddings) and stored at **Pinecone**.
- **Metadata Filtering**: Each chunk of data is tagged with Metadata (ex: `type: "weapon"`, `class: "hunter"`, `game: "ngs"`). When a user asks about NGS, the Bot filters out Base game data to avoid confusion.

## 3. Cross-lingual RAG Strategy

A major challenge: Users ask in **Vietnamese**, but all standard documentation is in **English**.

Workflow (Cross-lingual Flow):
1. **Query Translation**: Receives Vietnamese query -> LLM silently translates it to English.
2. **English Retrieval**: Uses the English sentence to search the Vector DB. Ensures accuracy of specialized keywords (e.g., "Potency", "Floor Damage").
3. **English Comprehension**: AI comprehends the retrieved English knowledge snippets.
4. **Vietnamese Generation**: AI synthesizes the answer and translates it smoothly into Vietnamese. Keeps Item and Skill names in original English, while explaining in Vietnamese.
   *(Example: "Bạn nên dùng skill **Fear Eraser** vì nó cho phép gây sát thương liên tục...")*
