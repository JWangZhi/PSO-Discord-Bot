"""Environment-backed configuration.

Keep API keys and deployment-specific values here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Discord
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

# AI Providers
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Context Compression
COMPRESSION_THRESHOLD = int(os.getenv("COMPRESSION_THRESHOLD", "10"))
GEMINI_API_KEY_EMBEDDING = os.getenv("GEMINI_API_KEY_EMBEDDING", "")

# RAG / Embeddings (LM Studio local)
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
LOCAL_EMBED_URL = os.getenv("LOCAL_EMBED_URL", "http://127.0.0.1:9707/v1")
LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", "qwen3-embedding-0.6b")

# Database
MONGODB_URI = os.getenv("MONGODB_URI", "")
