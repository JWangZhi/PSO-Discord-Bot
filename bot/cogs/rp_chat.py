"""
RP Channel Manager

Tracks which Discord channels are designated as roleplay channels.
RP channels respond to all messages (no @mention required) and use
channel-level shared memory so the whole channel shares one conversation.
"""

import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.db import COL_GUILD_CONFIG

log = logging.getLogger(__name__)


class RPChannelManager:
    """Manages roleplay-enabled channels per guild.

    Guild config document stores RP channels as a list of channel ID strings:
        { "guild_id": "123", "rp_channels": ["456", "789"], ... }

    An in-memory cache (guild_id int → set[channel_id int]) is populated on
    startup and kept in sync with every enable/disable call.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self._col = db[COL_GUILD_CONFIG]
        self._cache: dict[int, set[int]] = {}

    async def load_all(self) -> None:
        """Populate the in-memory cache from DB on startup."""
        self._cache.clear()
        async for doc in self._col.find(
            {"rp_channels": {"$exists": True, "$not": {"$size": 0}}}
        ):
            try:
                guild_id = int(doc["guild_id"])
                channel_ids = {int(c) for c in doc.get("rp_channels", [])}
                if channel_ids:
                    self._cache[guild_id] = channel_ids
            except (ValueError, KeyError) as exc:
                log.warning("[RPChannelManager] Skipping corrupt guild_config doc: %s", exc)
        log.info("[RPChannelManager] Loaded %d guild(s) with RP channels", len(self._cache))

    def is_rp_channel(self, guild_id: int, channel_id: int) -> bool:
        """Return True if the channel is a designated RP channel."""
        return channel_id in self._cache.get(guild_id, set())

    async def enable(self, guild_id: int, channel_id: int) -> None:
        """Designate a channel as an RP channel."""
        gid = str(guild_id)
        cid = str(channel_id)
        await self._col.update_one(
            {"guild_id": gid},
            {"$addToSet": {"rp_channels": cid}},
            upsert=True,
        )
        self._cache.setdefault(guild_id, set()).add(channel_id)
        log.info("[RPChannelManager] RP enabled: channel %s in guild %s", cid, gid)

    async def disable(self, guild_id: int, channel_id: int) -> None:
        """Remove RP designation from a channel."""
        gid = str(guild_id)
        cid = str(channel_id)
        await self._col.update_one(
            {"guild_id": gid},
            {"$pull": {"rp_channels": cid}},
        )
        self._cache.get(guild_id, set()).discard(channel_id)
        log.info("[RPChannelManager] RP disabled: channel %s in guild %s", cid, gid)

    def list_channels(self, guild_id: int) -> list[int]:
        """Return all RP channel IDs for a guild (from cache)."""
        return sorted(self._cache.get(guild_id, set()))
