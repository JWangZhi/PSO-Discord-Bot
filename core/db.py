"""
MongoDB connection, schema definitions, and index management.

Centralizes all database configuration so collections and indexes
are defined in one place and created automatically on startup.
"""

import logging
from datetime import datetime, timezone

import certifi
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import IndexModel, ASCENDING, DESCENDING

from settings import env as config
from settings import app as app_settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Collection names (single source of truth)
# ---------------------------------------------------------------------------
COL_RP_MEMORY = app_settings.MONGO_MEMORY_COLLECTION  # "rp_memory"
COL_GUILD_CONFIG = "guild_config"
COL_COMMAND_HISTORY = "command_history"

# ---------------------------------------------------------------------------
# Index definitions per collection
# ---------------------------------------------------------------------------
_INDEXES: dict[str, list[IndexModel]] = {
    COL_RP_MEMORY: [
        IndexModel([("channel_id", ASCENDING)], unique=True),
        IndexModel([("last_updated", DESCENDING)]),
    ],
    COL_GUILD_CONFIG: [
        IndexModel([("guild_id", ASCENDING)], unique=True),
    ],
    COL_COMMAND_HISTORY: [
        IndexModel([("timestamp", DESCENDING)]),
        IndexModel([("guild_id", ASCENDING), ("timestamp", DESCENDING)]),
        IndexModel([("user_id", ASCENDING), ("timestamp", DESCENDING)]),
        IndexModel(
            [("timestamp", ASCENDING)],
            name="ttl_90d",
            expireAfterSeconds=90 * 24 * 3600,  # auto-delete after 90 days
        ),
    ],
}


# ---------------------------------------------------------------------------
# Singleton connection
# ---------------------------------------------------------------------------
class MongoDB:
    """Async singleton managing the Motor client and database handle."""

    _client: AsyncIOMotorClient | None = None
    _db: AsyncIOMotorDatabase | None = None

    @classmethod
    def connect(cls) -> AsyncIOMotorDatabase:
        if cls._client is None:
            cls._client = AsyncIOMotorClient(
                config.MONGODB_URI,
                tlsCAFile=certifi.where(),
            )
            cls._db = cls._client[app_settings.APP_DB_NAME]
            log.info("[MongoDB] Connected to database: %s", app_settings.APP_DB_NAME)
        return cls._db

    @classmethod
    def get_db(cls) -> AsyncIOMotorDatabase:
        if cls._db is None:
            return cls.connect()
        return cls._db

    @classmethod
    async def close(cls) -> None:
        if cls._client:
            cls._client.close()
            cls._client = None
            cls._db = None
            log.info("[MongoDB] Connection closed.")

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------
    @classmethod
    async def ensure_indexes(cls) -> None:
        """Create all defined indexes (idempotent — safe to call every startup)."""
        db = cls.get_db()
        for col_name, models in _INDEXES.items():
            col = db[col_name]
            try:
                await col.create_indexes(models)
                log.info("[MongoDB] Indexes ensured for '%s'", col_name)
            except Exception as exc:
                log.warning("[MongoDB] Index creation failed for '%s': %s", col_name, exc)


# ---------------------------------------------------------------------------
# Default document factories
# ---------------------------------------------------------------------------
def default_rp_memory(channel_id: str) -> dict:
    """Default rp_memory document."""
    return {
        "channel_id": channel_id,
        "recent_messages": [],
        "summary": "",
        "facts": [],
        "emotion": {"mood": "neutral", "trust": 0.5},
        "character": "default",
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def default_guild_config(guild_id: str) -> dict:
    """Default guild_config document."""
    return {
        "guild_id": guild_id,
        "persona_name": "default",
        "language": "en",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
