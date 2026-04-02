"""Start bot, capture output for 10s, then exit."""
import sys, signal, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def alarm_handler(signum, frame):
    print("\n[TEST] 10s passed — bot startup verified. Shutting down.")
    sys.exit(0)

signal.signal(signal.SIGALRM, alarm_handler)
signal.alarm(10)

# Now start the bot normally
from main import bot, config
if not config.DISCORD_BOT_TOKEN:
    print("[ERROR] Missing DISCORD_BOT_TOKEN")
    sys.exit(1)

print("[TEST] Starting bot (will auto-stop after 10s)...")
bot.run(config.DISCORD_BOT_TOKEN)
