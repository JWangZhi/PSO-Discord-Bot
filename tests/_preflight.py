"""Pre-flight check: can the bot start?"""
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=== Import Check ===")
errors = []

for label, mod_path in [
    ("settings",    "settings.env"),
    ("settings",    "settings.app"),
    ("MCPBridge",   "core.mcp.mcp_client"),
    ("RouterAgent", "core.agents.router_agent"),
    ("ChatAgent",   "core.agents.chat_agent"),
    ("VisionAgent", "core.agents.vision_agent"),
    ("MemoryManager","core.memory"),
    ("MongoDB",     "core.db"),
    ("telemetry",   "core.telemetry"),
]:
    try:
        __import__(mod_path)
        print(f"  [OK] {label} ({mod_path})")
    except Exception as e:
        print(f"  [FAIL] {label} ({mod_path}): {e}")
        errors.append(label)

print("\n=== Config Check ===")
from settings import env as config
from settings import app as app_settings

for name, val in [
    ("DISCORD_BOT_TOKEN", config.DISCORD_BOT_TOKEN),
    ("GEMINI_API_KEY",    config.GEMINI_API_KEY),
    ("GROQ_API_KEY",      config.GROQ_API_KEY),
    ("MONGODB_URI",       config.MONGODB_URI),
    ("PINECONE_API_KEY",  config.PINECONE_API_KEY),
]:
    print(f"  {name}: {'SET' if val else 'EMPTY'}")

print(f"  RAG_ENABLED: {app_settings.RAG_ENABLED}")
print(f"  LOCAL_EMBED_URL: {config.LOCAL_EMBED_URL}")

print("\n=== Summary ===")
if errors:
    print(f"BLOCKED — failed imports: {errors}")
    sys.exit(1)
else:
    print("All imports OK. Bot can start (with required .env keys).")
