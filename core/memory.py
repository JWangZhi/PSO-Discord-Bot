"""
Memory Manager — CRUD operations for the 2-Layer RP Memory System.

Uses the shared MongoDB singleton from core.db.
"""
# pylint: disable=unsubscriptable-object

import logging
from datetime import datetime, timezone

from core.db import MongoDB, COL_RP_MEMORY, COL_RP_HISTORY, default_rp_memory
from settings import app as app_settings

log = logging.getLogger(__name__)


class MemoryManager:
    """Manages Short-term & Long-term Memory for RP.

    Each channel (or user) has 1 document containing:
    - recent_messages: 6 most recent messages (Short-term)
    - summary: Accumulated summary (Long-term)
    - facts: Fixed information about user
    - emotion: Bot's current emotional state
    - character: Name of the active persona
    """

    MAX_BUFFER = app_settings.MEMORY_MAX_BUFFER

    def __init__(self):
        self._db = MongoDB.get_db()
        self._col = self._db[COL_RP_MEMORY]
        self._history_col = self._db[COL_RP_HISTORY]

    async def get_memory(self, channel_id: str) -> dict:
        """Get all memory for a channel.

        Returns:
            Memory document or default structure if not found.
        """
        try:
            doc = await self._col.find_one({"channel_id": channel_id})
            if doc is None:
                return self._default_memory(channel_id)
            return doc
        except Exception:
            log.warning("[Memory] DB unreachable, using default memory for %s", channel_id)
            return self._default_memory(channel_id)

    async def get_recent_messages(self, channel_id: str, limit: int = 4) -> list[dict]:
        """Return the most recent messages for a channel/user."""
        memory = await self.get_memory(channel_id)
        recent = memory.get("recent_messages", [])
        if limit <= 0:
            return []
        return recent[-limit:]

    async def add_message(self, channel_id: str, role: str, content: str, persona: str = "default"):
        """Add 1 message to Short-term Memory and append to rp_history log.

        If MAX_RECENT is exceeded, the oldest message will be removed.

        Args:
            channel_id: Discord channel ID.
            role: "user" or "assistant".
            content: Message content.
            persona: Active persona name (logged to history).
        """
        # 1. Append to raw history log (fire-and-forget, never blocks LLM)
        await self.log_message(channel_id, role, content, persona)
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # 2. Update rp_memory (working memory for LLM), capped at MAX_RECENT
        try:
            await self._col.update_one(
                {"channel_id": channel_id},
                {
                    "$push": {
                        "recent_messages": {
                            "$each": [message],
                            "$slice": -self.MAX_BUFFER,
                        }
                    },
                    "$set": {"last_updated": datetime.now(timezone.utc).isoformat()},
                    "$setOnInsert": {
                        "summary": "",
                        "facts": [],
                        "emotion": {"mood": "neutral", "trust": 0.5},
                        "character": "default",
                    },
                },
                upsert=True,
            )
        except Exception:
            log.warning("[Memory] DB unreachable, skipping add_message for %s", channel_id)

    async def update_summary(self, channel_id: str, new_summary: str):
        """Update Long-term Summary (after Context Compression)."""
        try:
            await self._col.update_one(
                {"channel_id": channel_id},
                {
                    "$set": {
                        "summary": new_summary,
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                    }
                },
                upsert=True,
            )
        except Exception:
            log.warning("[Memory] DB unreachable, skipping update_summary for %s", channel_id)

    async def add_fact(self, channel_id: str, fact: str):
        """Add 1 new fact to Long-term Memory."""
        try:
            await self._col.update_one(
                {"channel_id": channel_id},
                {"$addToSet": {"facts": fact}},
                upsert=True,
            )
        except Exception:
            log.warning("[Memory] DB unreachable, skipping add_fact for %s", channel_id)

    async def clear_memory(self, channel_id: str):
        """Delete all memory for a channel/user."""
        try:
            await self._col.delete_one({"channel_id": channel_id})
            log.info("[Memory] Cleared memory for %s", channel_id)
        except Exception:
            log.warning("[Memory] DB unreachable, skipping clear_memory for %s", channel_id)

    async def update_emotion(self, channel_id: str, mood: str, trust: float):
        """Update bot's emotional state."""
        try:
            await self._col.update_one(
                {"channel_id": channel_id},
                {
                    "$set": {
                        "emotion.mood": mood,
                        "emotion.trust": max(0.0, min(1.0, trust)),
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                    }
                },
                upsert=True,
            )
        except Exception:
            log.warning("[Memory] DB unreachable, skipping update_emotion for %s", channel_id)

    async def set_character(self, channel_id: str, character_name: str):
        """Change Persona for the channel."""
        try:
            await self._col.update_one(
                {"channel_id": channel_id},
                {"$set": {"character": character_name}},
                upsert=True,
            )
        except Exception:
            log.warning("[Memory] DB unreachable, skipping set_character for %s", channel_id)

    async def get_game_version_pref(self, channel_id: str) -> str | None:
        """Return the saved game version preference for this channel, or None if not set."""
        doc = await self.get_memory(channel_id)
        return doc.get("game_version_pref")  # "ngs" | "pso2" | "both" | None

    async def set_game_version_pref(self, channel_id: str, version: str) -> None:
        """Persist the user's game version preference ("ngs", "pso2", or "both") for this channel."""
        try:
            await self._col.update_one(
                {"channel_id": channel_id},
                {"$set": {
                    "game_version_pref": version,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
            log.info("[Memory] game_version_pref=%s saved for %s", version, channel_id)
        except Exception:
            log.warning("[Memory] DB unreachable, skipping set_game_version_pref for %s", channel_id)

    async def log_message(
        self, channel_id: str, role: str, content: str, persona: str = "default"
    ) -> None:
        """Append a raw message to rp_history (append-only audit log).

        This is separate from rp_memory and is never fed to the LLM directly.
        Used to rebuild rp_memory if it gets wiped or corrupted.
        """
        entry = {
            "channel_id": channel_id,
            "role": role,
            "content": content,
            "persona": persona,
            "timestamp": datetime.now(timezone.utc),
        }
        try:
            await self._history_col.insert_one(entry)
        except Exception:
            log.warning("[Memory] DB unreachable, skipping log_message for %s", channel_id)

    async def rebuild_from_history(self, channel_id: str, limit: int = 20) -> bool:
        """Rebuild rp_memory from rp_history when memory is missing or stale.

        Fetches the last `limit` messages from rp_history, reconstructs
        recent_messages, then triggers ContextCompressor to regenerate summary.

        Returns True if rebuild succeeded, False if no history was found.
        """
        try:
            cursor = (
                self._history_col.find({"channel_id": channel_id})
                .sort("timestamp", -1)
                .limit(limit)
            )
            raw_docs = await cursor.to_list(length=limit)
        except Exception:
            log.warning("[Memory] DB unreachable, cannot rebuild history for %s", channel_id)
            return False

        if not raw_docs:
            log.info("[Memory] No history found for %s, nothing to rebuild", channel_id)
            return False

        # Reverse to chronological order
        raw_docs.reverse()

        # Reconstruct recent_messages (last MAX_BUFFER entries)
        recent = [
            {
                "role": doc["role"],
                "content": doc["content"],
                "timestamp": doc["timestamp"].isoformat()
                if hasattr(doc["timestamp"], "isoformat")
                else doc["timestamp"],
            }
            for doc in raw_docs
        ]
        recent = recent[-self.MAX_BUFFER :]

        try:
            await self._col.update_one(
                {"channel_id": channel_id},
                {
                    "$set": {
                        "recent_messages": recent,
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                    },
                    "$setOnInsert": {
                        "summary": "",
                        "facts": [],
                        "emotion": {"mood": "neutral", "trust": 0.5},
                        "character": "default",
                    },
                },
                upsert=True,
            )
            log.info(
                "[Memory] Rebuilt rp_memory for %s from %d history entries",
                channel_id,
                len(raw_docs),
            )
            return True
        except Exception:
            log.warning("[Memory] DB unreachable, cannot write rebuilt memory for %s", channel_id)
            return False

    @staticmethod
    def _default_memory(channel_id: str) -> dict:
        """Default memory structure."""
        return default_rp_memory(channel_id)


# --- Quick Test ---
if __name__ == "__main__":
    import asyncio

    async def test():
        print("=== Test MongoDB Connection ===")
        db = MongoDB.get_db()

        # Test ping
        result = await db.command("ping")
        print(f"[MongoDB] Ping: {result}")

        # Test MemoryManager
        mm = MemoryManager()
        test_channel = "test_ch_001"

        await mm.add_message(test_channel, "user", "Xin chào Matoi!")
        await mm.add_message(test_channel, "assistant", "Chào Guardian! Hôm nay bạn khỏe không?")
        await mm.add_fact(test_channel, "User tên là Kai")
        await mm.update_emotion(test_channel, "happy", 0.8)

        memory = await mm.get_memory(test_channel)
        print(f"\nMemory cho channel {test_channel}:")
        print(f"  Messages: {len(memory.get('recent_messages', []))}")
        print(f"  Facts: {memory.get('facts', [])}")
        print(f"  Emotion: {memory.get('emotion', {})}")

        # Cleanup test data
        await mm.clear_memory(test_channel)
        print("\n[Test] Test data cleaned up.")

        await MongoDB.close()

    asyncio.run(test())
