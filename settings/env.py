"""Environment-backed configuration.

Keep API keys and deployment-specific values here.
"""

import os

# load_dotenv() is called in settings/__init__.py

# Discord
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

# AI Providers
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Context Compression
COMPRESSION_THRESHOLD = int(os.getenv("COMPRESSION_THRESHOLD", "10"))
GEMINI_API_KEY_EMBEDDING = os.getenv("GEMINI_API_KEY_EMBEDDING", "")

# Database
MONGODB_URI = os.getenv("MONGODB_URI", "")
