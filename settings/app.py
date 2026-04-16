"""Application-level operational settings.

Keep non-secret defaults here so constants are not scattered in modules.
"""

import os


# Discord / runtime
DISCORD_MESSAGE_LIMIT = int(os.getenv("DISCORD_MESSAGE_LIMIT", "2000"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))


# Router
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "gemini-2.5-flash")
ROUTER_TEMPERATURE = float(os.getenv("ROUTER_TEMPERATURE", "0.1"))

# Fast route: exact greetings that skip LLM entirely
ROUTER_SIMPLE_CHAT_TERMS: frozenset[str] = frozenset({
    "hello", "hi", "hey", "sup", "yo", "hiya", "heya",
    "chào", "xin chào", "ê", "ê bot", "bot ơi",
    "gm", "gn", "good morning", "good night",
    "thanks", "thank you", "ty", "thx", "cảm ơn",
    "ok", "okay", "k", "kk",
    "bye", "bb", "cya", "goodbye", "tạm biệt",
    "lol", "lmao", "haha", "hehe", "xd",
})

# Fast route: regex patterns that skip LLM (checked after stripping @mention)
# These catch greetings with trailing punctuation, names, etc.
ROUTER_FAST_CHAT_PATTERNS: tuple[str, ...] = (
    r"^(hi|hey|hello|yo|sup|hiya|heya|chào|xin chào)[\s!.,?]*\w{0,20}[!.?]*$",
    r"^(good\s*(morning|night|evening|day))[\s!.,]*$",
    r"^(thanks?|thank\s*you|ty|thx|cảm ơn)[\s!.,]*.*$",
    r"^(bye|goodbye|cya|bb|tạm biệt)[\s!.,]*$",
    r"^[hHaAeElL]{2,6}$",  # "haha", "hehe", "lol" etc.
)


# MongoDB (operational data only — user memory, session state)
APP_DB_NAME = os.getenv("APP_DB_NAME", "pso2_bot")
MONGO_MEMORY_COLLECTION = os.getenv("MONGO_MEMORY_COLLECTION", "rp_memory")


# Memory
MEMORY_MAX_BUFFER = int(os.getenv("MEMORY_MAX_BUFFER", "30"))


# Vision
LOCAL_VLM_URL = os.getenv("LOCAL_VLM_URL", "http://127.0.0.1:9707/v1")
LOCAL_VLM_MODEL = os.getenv("LOCAL_VLM_MODEL", "local-model")
VISION_MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "512"))
VISION_TEMPERATURE = float(os.getenv("VISION_TEMPERATURE", "0.2"))


# Debug
BOT_DEBUG_ENABLED = os.getenv("BOT_DEBUG_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
BOT_DEBUG_INCLUDE_IN_REPLY = os.getenv("BOT_DEBUG_INCLUDE_IN_REPLY", "0").lower() in {"1", "true", "yes", "on"}
