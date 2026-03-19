"""
MongoDB Connection & Memory Manager

This module manages the MongoDB Atlas connection and provides CRUD operations
for the 2-Layer Memory System (Short-term & Long-term Memory)
used for the Role Play feature.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from motor.motor_asyncio import AsyncIOMotorClient

import config

# Database & Collection names
DB_NAME = "pso2_bot"
COLLECTION_MEMORY = "rp_memory"


class MongoDB:
    """Singleton managing async connection to MongoDB Atlas."""

    _client: AsyncIOMotorClient | None = None
    _db = None

    @classmethod
    def connect(cls):
        """Initialize connection to MongoDB Atlas."""
        if cls._client is None:
            cls._client = AsyncIOMotorClient(config.MONGODB_URI)
            cls._db = cls._client[DB_NAME]
            print(f"[MongoDB] Connected to database: {DB_NAME}")
        return cls._db

    @classmethod
    def get_db(cls):
        """Get database object (auto-connect if not connected)."""
        if cls._db is None:
            return cls.connect()
        return cls._db

    @classmethod
    async def close(cls):
        """Close connection."""
        if cls._client:
            cls._client.close()
            cls._client = None
            cls._db = None
            print("[MongoDB] Connection closed.")


class MemoryManager:
    """Manages Short-term & Long-term Memory for RP.

    Each channel (or user) has 1 document containing:
    - recent_messages: 6 most recent messages (Short-term)
    - summary: Accumulated summary (Long-term)
    - facts: Fixed information about user
    - emotion: Bot's current emotional state
    - character: Name of the active persona
    """

    MAX_BUFFER = 30  # Maximum messages to keep before hard cap (allows buffering for compression)

    def __init__(self):
        self._db = MongoDB.get_db()
        self._col = self._db[COLLECTION_MEMORY]

    async def get_memory(self, channel_id: str) -> dict:
        """Get all memory for a channel.

        Returns:
            Memory document or default structure if not found.
        """
        doc = await self._col.find_one({"channel_id": channel_id})
        if doc is None:
            return self._default_memory(channel_id)
        return doc

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

    async def update_summary(self, channel_id: str, new_summary: str):
        """Update Long-term Summary (after Context Compression)."""
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

    async def add_fact(self, channel_id: str, fact: str):
        """Add 1 new fact to Long-term Memory."""
        await self._col.update_one(
            {"channel_id": channel_id},
            {"$addToSet": {"facts": fact}},
            upsert=True,
        )

    async def update_emotion(self, channel_id: str, mood: str, trust: float):
        """Update bot's emotional state."""
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

    async def set_character(self, channel_id: str, character_name: str):
        """Change Persona for the channel."""
        await self._col.update_one(
            {"channel_id": channel_id},
            {"$set": {"character": character_name}},
            upsert=True,
        )

    async def clear_memory(self, channel_id: str):
        """Delete all memory for a channel (reset)."""
        await self._col.delete_one({"channel_id": channel_id})

    @staticmethod
    def _default_memory(channel_id: str) -> dict:
        """Default memory structure."""
        return {
            "channel_id": channel_id,
            "recent_messages": [],
            "summary": "",
            "facts": [],
            "emotion": {"mood": "neutral", "trust": 0.5},
            "character": "default",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }


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
