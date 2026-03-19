"""Central configuration - Load environment variables from .env"""

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

# Local Embedding (LM Studio)
LOCAL_EMBED_URL = os.getenv("LOCAL_EMBED_URL", "http://127.0.0.1:1234/v1")
LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", "text-embedding-embeddinggemma-300m-qat")

# Database
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
MONGODB_URI = os.getenv("MONGODB_URI", "")
