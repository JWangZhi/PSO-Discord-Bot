"""Discord client for the Base/NGS advisor bot."""

import asyncio
import time

import discord
from discord import app_commands

from bot.cogs.rp_chat import RPChannelManager
from bot.discord_utils import reply_long
from core.agents.chat_agent import ChatAgent
from core.agents.router_agent import RouterAgent
from core.agents.vision_agent import VisionAgent
from core.context_compressor import ContextCompressor
from core.db import MongoDB
from core.formatters.wiki_direct_replies import DirectWikiReplyRenderer
from core.game_versions import CODE_VERSION, GameVersionResolver, game_label as format_game_label
from core.mcp.mcp_client import MCPBridge
from core.memory import MemoryManager
from core.telemetry import (
    EXTERNAL_API_ERRORS,
    INTENT_REQUESTS,
    MESSAGE_PROCESSING_TIME,
    start_metrics_server,
)
from core.wiki_answer_service import WikiAnswerService
from core.wiki_query import FACTUAL_QUERY_HINTS, KNOWN_CLASSES, STOPWORDS, WikiQueryProcessor
from core.wiki_search import WikiSearchService
from settings import app as app_settings


class PSO2Bot(discord.Client):
    """Main Discord client for Base/NGS bot."""

    def __init__(self, *, enable_mcp: bool = False):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.router = RouterAgent()
        self.memory = MemoryManager()
        self.chat_agent = ChatAgent(self.memory)
        self.vision = VisionAgent()
        self.compressor = ContextCompressor()
        self.enable_mcp = enable_mcp
        self.mcp = MCPBridge() if enable_mcp else None
        self.wiki_search = WikiSearchService()
        self.rp_manager = RPChannelManager(MongoDB.get_db())
        self.gv_resolver = GameVersionResolver(self.memory)
        self.query = WikiQueryProcessor(self.memory)
        self.direct_replies = DirectWikiReplyRenderer(
            known_classes=KNOWN_CLASSES,
            stopwords=STOPWORDS,
            factual_query_hints=FACTUAL_QUERY_HINTS,
        )
        self.wiki_answers = WikiAnswerService(
            memory=self.memory,
            chat_agent=self.chat_agent,
            wiki_search=self.wiki_search,
            query=self.query,
            direct_replies=self.direct_replies,
            mcp=self.mcp,
            game_label=self.game_label,
        )

    @staticmethod
    def game_label(game_key: str) -> str:
        """Return the user-facing game label for an internal game key."""
        return format_game_label(game_key)

    def build_debug_suffix(self, flow_name: str, intent: str | None = None) -> str:
        """Build debug lines for terminal and optional Discord output."""
        if not app_settings.BOT_DEBUG_ENABLED:
            return ""

        snap = self.chat_agent.get_last_debug_snapshot()
        lines = [
            f"flow={flow_name}",
            f"intent={intent or '-'}",
            "wiki_search=enabled",
            f"insufficient_evidence={snap.get('insufficient_evidence')}",
            f"retrieval_quality={snap.get('retrieval_quality', '-')}",
            f"llm_mode={snap.get('llm_mode', '-')}",
            f"reply_len={snap.get('reply_len', 0)}",
        ]

        print("[BOT_DEBUG] " + " | ".join(lines))

        if not app_settings.BOT_DEBUG_INCLUDE_IN_REPLY:
            return ""

        return "\n\n```text\n[BOT_DEBUG]\n" + "\n".join(lines) + "\n```"

    async def _handle_pending_reply(
        self, session_id: str, content: str, message: discord.Message
    ) -> bool:
        """Handle replies to version clarification prompts."""
        mem = await self.memory.get_memory(session_id)
        recent = mem.get("recent_messages", [])
        pending_query: str | None = None
        pending_switch: tuple[str, str, str] | None = None

        for msg in reversed(recent[-6:]):
            if msg.get("role") != "system":
                continue
            c = msg.get("content", "")
            if c.startswith("[PENDING_WIKI_QUERY]"):
                pending_query = c[len("[PENDING_WIKI_QUERY]"):].strip()
                break
            if c.startswith("[PENDING_SWITCH]"):
                parts = {}
                rest = c[len("[PENDING_SWITCH]"):].strip()
                for chunk in rest.split(" ", 2):
                    if "=" in chunk:
                        k, v = chunk.split("=", 1)
                        parts[k] = v
                pending_switch = (
                    parts.get("old", "ngs"),
                    parts.get("new", "pso2"),
                    parts.get("query", ""),
                )
                break

        if pending_query is not None:
            version = self.gv_resolver._parse_clarification(content)
            if version is None:
                return False

            cleaned = [m for m in recent if not m.get("content", "").startswith("[PENDING_WIKI_QUERY]")]
            await self.memory._col.update_one(
                {"channel_id": session_id},
                {"$set": {"recent_messages": cleaned}},
            )

            search_query = await self.query.expand_query(session_id, pending_query)
            if version == "both":
                await self.memory.set_game_version_pref(session_id, "both")
                extra = await self.wiki_answers.retrieve_both_context(search_query)
                reply = await self.wiki_answers.reply(session_id, pending_query, extra)
                reply = "✅ Got it! I'll compare **Base** and **NGS** by default for this channel.\n\n" + reply
                await reply_long(message, reply)
                return True

            await self.memory.set_game_version_pref(session_id, version)
            label = GameVersionResolver.VERSION_LABELS[version]
            extra = await self.wiki_answers.retrieve_context(search_query, version, label)
            reply = await self.wiki_answers.reply(session_id, pending_query, extra)
            reply = f"✅ Got it! I'll use **{label}** as your default for this channel.\n\n" + reply
            await reply_long(message, reply)
            return True

        if pending_switch is not None:
            answer = self.gv_resolver._parse_switch_reply(content)
            if answer is None:
                return False
            old_ver, new_ver, original_query = pending_switch
            cleaned = [m for m in recent if not m.get("content", "").startswith("[PENDING_SWITCH]")]
            await self.memory._col.update_one(
                {"channel_id": session_id},
                {"$set": {"recent_messages": cleaned}},
            )
            if answer:
                await self.memory.clear_memory(session_id)
                await self.memory.set_game_version_pref(session_id, new_ver)
                label = GameVersionResolver.VERSION_LABELS[new_ver]
                await message.reply(f"🔄 Session reset! Switched to **{label}**.")
            else:
                new_ver = old_ver
                label = GameVersionResolver.VERSION_LABELS[new_ver]
                await message.reply(f"👍 Keeping **{label}** session.")
            if original_query:
                label = GameVersionResolver.VERSION_LABELS[new_ver]
                search_query = await self.query.expand_query(session_id, original_query)
                if new_ver == "both":
                    extra = await self.wiki_answers.retrieve_both_context(search_query)
                else:
                    extra = await self.wiki_answers.retrieve_context(search_query, new_ver, label)
                reply = await self.wiki_answers.reply(session_id, original_query, extra)
                await reply_long(message, reply)
            return True

        return False

    async def _handle_wiki_message(
        self,
        session_id: str,
        content: str,
        message: discord.Message,
        *,
        router_suggestion: str = "ngs",
        flow_name: str = "on_message",
    ) -> None:
        """Resolve game mode, retrieve wiki context, and reply to a message."""
        game_key, pending = await self.gv_resolver.resolve(
            session_id, content, router_suggestion
        )

        if pending == "ask_version":
            await self.memory.add_message(
                session_id,
                "system",
                f"[PENDING_WIKI_QUERY] {content}",
            )
            await message.reply(GameVersionResolver.ASK_MSG)
            return

        if pending and pending.startswith("ask_switch:"):
            _, old_ver, new_ver = pending.split(":")
            old_label = GameVersionResolver.VERSION_LABELS[old_ver]
            new_label = GameVersionResolver.VERSION_LABELS[new_ver]
            await self.memory.add_message(
                session_id,
                "system",
                f"[PENDING_SWITCH] old={old_ver} new={new_ver} query={content}",
            )
            await message.reply(
                GameVersionResolver.SWITCH_MSG.format(old=old_label, new=new_label)
            )
            return

        search_query = await self.query.expand_query(session_id, content)
        if game_key == "both":
            extra = await self.wiki_answers.retrieve_both_context(search_query)
        else:
            game_label = self.game_label(game_key or "ngs")
            extra = await self.wiki_answers.retrieve_context(search_query, game_key or "ngs", game_label)
        reply = await self.wiki_answers.reply(session_id, content, extra)
        reply += self.build_debug_suffix(flow_name=flow_name, intent="wiki_search")
        await reply_long(message, reply)

    async def setup_hook(self):
        """Sync slash commands on bot startup."""
        try:
            await MongoDB.ensure_indexes()
            print("[OK] MongoDB indexes ensured.")
        except Exception as e:
            print(f"[WARN] MongoDB index setup failed (bot continues): {e}")

        await self.rp_manager.load_all()
        print("[OK] RP channel config loaded.")

        if self.mcp:
            await self.mcp.start()
            print("[OK] MCP Bridge connected.")
        else:
            print("[OK] MCP disabled (use --mcp to enable).")
        await self.tree.sync()
        print("[OK] Slash commands synced.")

    async def close(self):
        """Graceful shutdown."""
        if self.mcp:
            await self.mcp.close()
        await MongoDB.close()
        await super().close()

    async def on_ready(self):
        print(f"[OK] Bot is online: {self.user} (ID: {self.user.id})")
        print(f"[OK] Serving {len(self.guilds)} server(s).")
        print(f"[OK] Debug enabled: {app_settings.BOT_DEBUG_ENABLED}")
        print(f"[OK] Debug include in reply: {app_settings.BOT_DEBUG_INCLUDE_IN_REPLY}")
        print(f"[OK] Code version: {CODE_VERSION}")
        start_metrics_server(port=app_settings.METRICS_PORT)

    async def on_message(self, message: discord.Message):
        """Handle incoming messages and pass to intent router."""
        if message.author.bot:
            return

        is_rp = (
            message.guild is not None
            and self.rp_manager.is_rp_channel(message.guild.id, message.channel.id)
        )

        if is_rp:
            content = message.content.strip()
            if not content:
                return
            session_id = str(message.channel.id)
        else:
            if self.user not in message.mentions:
                return
            content = message.content.replace(f'<@{self.user.id}>', '').strip()
            session_id = str(message.author.id)

        has_image = any(
            att.content_type and "image" in att.content_type
            for att in message.attachments
        )

        async with message.channel.typing():
            start_time = time.time()
            try:
                if is_rp:
                    pending_handled = await self._handle_pending_reply(session_id, content, message)
                    if pending_handled:
                        MESSAGE_PROCESSING_TIME.labels(intent_type="wiki_search").observe(time.time() - start_time)
                        asyncio.create_task(self.compressor.run_compression(session_id))
                        return

                    if self.query.looks_like_wiki_query(content, has_image):
                        INTENT_REQUESTS.labels(intent_type="wiki_search").inc()
                        await self._handle_wiki_message(
                            session_id,
                            content,
                            message,
                            router_suggestion="ngs",
                            flow_name="rp_channel",
                        )
                        MESSAGE_PROCESSING_TIME.labels(intent_type="wiki_search").observe(time.time() - start_time)
                        asyncio.create_task(self.compressor.run_compression(session_id))
                        return

                    INTENT_REQUESTS.labels(intent_type="chat").inc()
                    reply = await self.chat_agent.generate_reply(session_id, content)
                    reply += self.build_debug_suffix(flow_name="rp_channel", intent="chat")
                    await reply_long(message, reply)
                    MESSAGE_PROCESSING_TIME.labels(intent_type="chat").observe(time.time() - start_time)
                    asyncio.create_task(self.compressor.run_compression(session_id))
                    return

                result = await asyncio.to_thread(self.router.classify_intent, content, has_image)

                pending_handled = await self._handle_pending_reply(session_id, content, message)
                if pending_handled:
                    return

                intent = result.intent
                if intent == "chat" and self.query.looks_like_wiki_query(content, has_image):
                    print("[ROUTER_FALLBACK] chat -> wiki_search (factual query heuristic)")
                    intent = "wiki_search"

                INTENT_REQUESTS.labels(intent_type=intent).inc()

                if intent == "fashion_match":
                    if has_image:
                        att = [a for a in message.attachments if a.content_type and "image" in a.content_type][0]
                        img_bytes = await att.read()
                        tags_str = await self.vision.analyze_outfit_from_bytes(img_bytes)
                        sys_msg = (
                            "[System Context] "
                            f"User uploaded an image. Vision analysis returned: {tags_str}. "
                            "Explain what is visible and suggest likely style keywords in English only."
                        )
                        reply = await self.chat_agent.generate_reply(session_id, content, extra_context=sys_msg)
                        await reply_long(message, reply)
                    else:
                        reply = await self.chat_agent.generate_reply(
                            session_id,
                            content,
                            extra_context="[System Context] User asked for a fashion match but forgot to upload an image. Subtly remind them.",
                        )
                        await reply_long(message, reply)
                elif intent == "wiki_search":
                    await self._handle_wiki_message(
                        session_id,
                        content,
                        message,
                        router_suggestion=result.game_version,
                        flow_name="on_message",
                    )
                else:
                    reply = await self.chat_agent.generate_reply(session_id, content)
                    reply += self.build_debug_suffix(flow_name="on_message", intent=intent)
                    await reply_long(message, reply)

                MESSAGE_PROCESSING_TIME.labels(intent_type=intent).observe(time.time() - start_time)
                asyncio.create_task(self.compressor.run_compression(session_id))
            except Exception as e:
                EXTERNAL_API_ERRORS.labels(service_name="router_llm").inc()
                print(f"[ERROR] Message processing failed for user {message.author.id}: {e}")
                await message.reply("❌ Something went wrong processing your request. Please try again.")
