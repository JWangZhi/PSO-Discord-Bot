"""
Memory Manager — CRUD operations for the 2-Layer RP Memory System.

Uses the shared MongoDB singleton from core.db.
"""
# pylint: disable=unsubscriptable-object

import logging
from datetime import datetime, timezone

from core.db import MongoDB, COL_RP_MEMORY, default_rp_memory
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

    async def add_message(self, channel_id: str, role: str, content: str):
        """Add 1 message to Short-term Memory.

        If MAX_RECENT is exceeded, the oldest message will be removed.

        Args:
            channel_id: Discord channel ID.
            role: "user" or "assistant".
            content: Message content.
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Add new message, capped at MAX_RECENT
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

    async def clear_memory(self, channel_id: str):
        """Delete all memory for a channel (reset)."""
        try:
            await self._col.delete_one({"channel_id": channel_id})
        except Exception:
            log.warning("[Memory] DB unreachable, skipping clear_memory for %s", channel_id)

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
