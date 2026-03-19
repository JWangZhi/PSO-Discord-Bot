# Cloud Infrastructure Plan (Zero Cost)

Instead of running on a personal computer (which consumes electricity and is unstable), we will leverage "Always Free" tiers from major cloud providers to operate the Bot 24/7 at $0 cost.

---

## 1. Hosting (Where the Bot lives)
Select VPS options that offer permanent free tiers (Always Free).

- **Option 1: Oracle Cloud (Best)**
  - **Specs**: 4 ARM Ampere Cores, 24GB RAM (Extremely generous tier).
  - **Pros**: Powerful enough to run the Bot, Database, and even some small AI models directly.
  - **Cons**: Requires a VISA/Mastercard for verification (no charges) and can be hard to register in some regions.
- **Option 2: Google Cloud Platform (GCP)**
  - **Specs**: `e2-micro` instance (2 vCPU, 1GB RAM).
  - **Pros**: Always free, very stable for running the Bot Core (Python/Node.js).
- **Option 3: Hugging Face Spaces**
  - **Specs**: Docker Container (Basic CPU, 16GB RAM).
  - **Pros**: Completely free, suitable for hosting Python applications.

---

## 2. AI Core (Artificial Intelligence via API)
Instead of running heavy Local models, we'll use free APIs from major providers.

- **Google Gemini API (Top Priority)**:
  - **Model**: Gemini 1.5 Flash.
  - **Limits**: Free ~1500 requests/day (More than enough for a starting Discord Bot).
  - **Pros**: Supports massive Context limits (up to 1M tokens), integrates well with RAG.
- **Groq API**:
  - **Model**: Llama 3 (70B/8B).
  - **Pros**: Near-instant response times (ultra-fast), very generous free tier.
- **Together AI**: Offers $25 credit on signup, enough for millions of tokens initially.

---

## 3. Database & Storage
- **User Data**: **MongoDB Atlas (M0 Tier)** - 512MB permanent free storage.
- **RAG Data (Vectors)**: **Pinecone (Starter Plan)** - 1 free index, enough for the entire Lore and Items of PSO2/NGS.

---

## 4. Cloud Management Focus

When migrating to the Cloud and using AI via API, hardware burdens (CPU/GPU) are eliminated, but you must focus on managing the **System (Scripts)** and **Data (Database)**:

### A. Environment Variable Management (`.env`)
- **Never** place API Keys (Gemini, Discord Token, MongoDB URI) directly into the code.
- Use the **Secrets/Environment Variables** feature of your Cloud Provider (e.g., GitHub Secrets, GCP Secret Manager).
- This is the "key to your wallet" (leaking your Gemini key could drain your tokens).

### B. Runtime Optimization for Scripts (Bot Core)
- **Restart Policy**: Configure the bot to auto-restart if it crashes (use Docker with `restart: always` or PM2).
- **Log Management**: Save logs to a file or use a simple log monitoring service to troubleshoot remotely without SSHing into the server.
- **Webhook vs Gateway**: With a weak Cloud instance (1GB RAM), consider using Discord Interactions (Webhooks) instead of maintaining continuous WebSocket connections to save resources.

### C. Database Security and Performance
- **IP Whitelisting**: Only allow the Bot Server's IP to access MongoDB Atlas/Pinecone.
- **Connection Pooling**: Ensure the database connection library uses connection reuse to avoid out-of-memory errors on small servers (`e2-micro`).

---

## 5. Deployment Workflow
1.  **Code**: Push to GitHub.
2.  **Deploy**: 
    - If using **Oracle/GCP**: Use Docker Compose to run the Bot.
    - If using **Hugging Face**: Automatically build Docker from GitHub.
3.  **AI Connection**: The Bot makes commands via API Keys (Gemini/Groq) -> Does not consume Server CPU/RAM to process AI.

---

## 6. Conclusion
By combining **GCP/Oracle** and the **Gemini API**, you get a professional, 24/7 online, highly intelligent AI Bot system **without spending a single dime on electricity or server rentals**.
