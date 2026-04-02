"""PSO2/NGS AI Advisor Bot - Entry Point"""

import argparse
import time
import asyncio
import discord
from discord import app_commands

from settings import env as config
from settings import app as app_settings
from core.agents.router_agent import RouterAgent
from core.agents.chat_agent import ChatAgent
from core.agents.vision_agent import VisionAgent
from core.memory import MemoryManager
from core.db import MongoDB
from core.mcp.mcp_client import MCPBridge
from core.context_compressor import ContextCompressor
from core.telemetry import (
    start_metrics_server, 
    INTENT_REQUESTS, 
    MESSAGE_PROCESSING_TIME, 
    COMMAND_REQUESTS,
    EXTERNAL_API_ERRORS
)

# ---------------------------------------------------------------------------
# CLI arguments
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="PSO2/NGS AI Advisor Bot")
parser.add_argument("--mcp", action="store_true", help="Enable MCP wiki fetching via Chrome DevTools")
cli_args = parser.parse_args()

class PSO2Bot(discord.Client):
    """Main Discord Client for PSO2/NGS Bot."""

    def __init__(self, *, enable_mcp: bool = False):
        intents = discord.Intents.default()
        intents.message_content = True # Required for reading messages
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.router = RouterAgent()
        self.memory = MemoryManager()
        self.chat_agent = ChatAgent(self.memory)
        self.vision = VisionAgent()
        self.compressor = ContextCompressor()
        self.enable_mcp = enable_mcp
        self.mcp = MCPBridge() if enable_mcp else None

        # RAG Pipeline (optional — requires Pinecone + LM Studio)
        self.rag = None
        if app_settings.RAG_ENABLED:
            try:
                from data.rag.rag_pipeline import RAGPipeline
                self.rag = RAGPipeline()
                print("[OK] RAG Pipeline initialized.")
            except Exception as e:
                print(f"[WARN] RAG Pipeline init failed (bot continues): {e}")

    @staticmethod
    def build_no_rag_context(game_label: str) -> str:
        """System context for game-specific queries."""
        return (
            f"[System Context] User is asking about {game_label}. "
            "CRITICAL: No wiki data was found for this query. "
            "You MUST NOT fabricate or guess any game data including skill names, "
            "stat values, damage numbers, or game mechanics. "
            "Respond with: 'I could not find verified data for this topic in the "
            "ARKS database. Please try a more specific query, or check the wiki "
            "directly at https://pso2na.arks-visiphone.com/wiki/' "
            "If you are confident the entity does not exist in the specified game "
            "version, state that clearly."
        )

    def build_debug_suffix(self, flow_name: str, intent: str | None = None) -> str:
        """Build debug lines for terminal and optional Discord output."""
        if not app_settings.BOT_DEBUG_ENABLED:
            return ""

        snap = self.chat_agent.get_last_debug_snapshot()
        lines = [
            f"flow={flow_name}",
            f"intent={intent or '-'}",
            f"rag_enabled=False",
            f"insufficient_evidence={snap.get('insufficient_evidence')}",
            f"retrieval_quality={snap.get('retrieval_quality', '-')}",
            f"llm_mode={snap.get('llm_mode', '-')}",
            f"reply_len={snap.get('reply_len', 0)}",
        ]

        print("[BOT_DEBUG] " + " | ".join(lines))

        if not app_settings.BOT_DEBUG_INCLUDE_IN_REPLY:
            return ""

        return "\n\n```text\n[BOT_DEBUG]\n" + "\n".join(lines) + "\n```"

    async def _retrieve_wiki_context(self, query: str, game_version: str, game_label: str) -> str:
        """Retrieve wiki context via RAG → MediaWiki API → model knowledge fallback chain."""
        # Entity-game validation (e.g., Phantom asked in NGS → redirect to PSO2)
        from core.mcp.mcp_client import validate_entity_game
        corrected_version, correction_warning = validate_entity_game(query, game_version)
        if correction_warning:
            game_version = corrected_version
            game_label = "PSO2 Classic" if game_version == "pso2" else "PSO2: New Genesis"

        prefix = correction_warning or ""

        # 1. Try RAG (cached knowledge)
        if self.rag:
            try:
                rag_context = await asyncio.to_thread(
                    self.rag.retrieve_context, query, game_version.upper()
                )
                if "[INSUFFICIENT_EVIDENCE]" not in rag_context:
                    return prefix + rag_context
            except Exception as e:
                print(f"[WARN] RAG retrieval failed: {e}")

        # 2. Fallback: Live wiki fetch via MediaWiki API
        if self.mcp:
            wiki_content = await self.mcp.search_wiki(query, game_version=game_version)
            if wiki_content:
                return prefix + (
                    "--- Live Wiki Data ---\n"
                    f"Game version: {game_label}\n"
                    "IMPORTANT: Base your answer ONLY on the following wiki content.\n"
                    "If sources are present, cite the source URL in your answer.\n\n"
                    f"{wiki_content}\n"
                )

        # 3. Final fallback: model knowledge only
        return prefix + self.build_no_rag_context(game_label)

    async def setup_hook(self):
        """Sync Slash Commands on bot startup."""
        # Database indexes
        try:
            await MongoDB.ensure_indexes()
            print("[OK] MongoDB indexes ensured.")
        except Exception as e:
            print(f"[WARN] MongoDB index setup failed (bot continues): {e}")

        # MCP bridge
        if self.mcp:
            await self.mcp.start()
            print("[OK] MCP Bridge connected.")
        else:
            print("[OK] MCP disabled (use --mcp to enable).")
        await self.tree.sync()
        print("[OK] Slash commands synced.")

    async def close(self):
        """Graceful shutdown — close MCP bridge, MongoDB, then Discord."""
        if self.mcp:
            await self.mcp.close()
        await MongoDB.close()
        await super().close()

    async def on_ready(self):
        print(f"[OK] Bot is online: {self.user} (ID: {self.user.id})")
        print(f"[OK] Serving {len(self.guilds)} server(s).")
        print(f"[OK] Debug enabled: {app_settings.BOT_DEBUG_ENABLED}")
        print(f"[OK] Debug include in reply: {app_settings.BOT_DEBUG_INCLUDE_IN_REPLY}")
        
        # Start Prometheus metrics server
        start_metrics_server(port=app_settings.METRICS_PORT)
        
    async def on_message(self, message: discord.Message):
        """Handle incoming messages and pass to Intent Router."""
        if message.author.bot:
            return
            
        # Only respond if the bot is mentioned
        if self.user not in message.mentions:
            return
            
        # Clean the message content (remove the bot mention)
        content = message.content.replace(f'<@{self.user.id}>', '').strip()
        
        has_image = False
        img_url = None
        if message.attachments:
            for att in message.attachments:
                if att.content_type and 'image' in att.content_type:
                    has_image = True
                    img_url = att.url
                    break
        
        # Determine intent
        async with message.channel.typing():
            start_time = time.time()
            try:
                # LLM call is synchronous, so wrap in to_thread to avoid blocking discord.py loop
                result = await asyncio.to_thread(self.router.classify_intent, content, has_image)
                
                # Record Metric: Intent resolved
                INTENT_REQUESTS.labels(intent_type=result.intent).inc()
                
                # Execute actual AI logic based on resolved Intent
                session_id = str(message.author.id)
                
                if result.intent == "fashion_match":
                    if has_image:
                        # Extract first image attachment
                        att = [a for a in message.attachments if a.content_type and 'image' in a.content_type][0]
                        img_bytes = await att.read()
                        
                        # 1. Vision Agent: Extract structural tags
                        tags_str = await self.vision.analyze_outfit_from_bytes(img_bytes)

                        # Vision-only explanation (no vector DB).
                        sys_msg = (
                            "[System Context] "
                            f"User uploaded an image. Vision analysis returned: {tags_str}. "
                            "Explain what is visible and suggest likely style keywords in English only."
                        )
                        reply = await self.chat_agent.generate_reply(session_id, content, extra_context=sys_msg)
                        await message.reply(reply)
                    else:
                        reply = await self.chat_agent.generate_reply(session_id, content, extra_context="[System Context] User asked for a fashion match but forgot to upload an image. Subtly remind them.")
                        await message.reply(reply)
                        
                elif result.intent == "wiki_search":
                    game_key = result.game_version
                    game_label = "PSO2 Classic" if game_key == "pso2" else "PSO2: New Genesis"
                    extra = await self._retrieve_wiki_context(content, game_key, game_label)
                    reply = await self.chat_agent.generate_reply(session_id, content, extra_context=extra)
                    reply += self.build_debug_suffix(flow_name="on_message", intent=result.intent)
                    await message.reply(reply)
                    
                else: # "chat"
                    # Default: Conversational memory utilizing the background Context Compressor natively
                    reply = await self.chat_agent.generate_reply(session_id, content)
                    reply += self.build_debug_suffix(flow_name="on_message", intent=result.intent)
                    await message.reply(reply)
                    
                # Record Metric: Processing Time
                MESSAGE_PROCESSING_TIME.labels(intent_type=result.intent).observe(time.time() - start_time)

                # Non-blocking compression check
                asyncio.create_task(self.compressor.run_compression(session_id))
                    
            except Exception as e:
                EXTERNAL_API_ERRORS.labels(service_name="router_llm").inc()
                print(f"[ERROR] Message processing failed for user {message.author.id}: {e}")
                await message.reply("❌ Something went wrong processing your request. Please try again.")


bot = PSO2Bot(enable_mcp=cli_args.mcp)

# ---------------------------------------------------------------------------
# Enum for Game Version selection
# ---------------------------------------------------------------------------
class GameVersion(discord.Enum):
    PSO2 = "pso2"
    NGS = "ngs"

# ---------------------------------------------------------------------------
# Slash Commands
# ---------------------------------------------------------------------------

async def send_long_message(interaction: discord.Interaction, text: str):
    """Split a long string into 2000-char chunks and send to Discord."""
    limit = app_settings.DISCORD_MESSAGE_LIMIT
    if len(text) <= limit:
        await interaction.followup.send(text)
        return

    lines = text.split("\n")
    current_chunk = ""

    for line in lines:
        # Handle single lines longer than the limit
        while len(line) > limit:
            if current_chunk:
                await interaction.followup.send(current_chunk)
                current_chunk = ""
            await interaction.followup.send(line[:limit])
            line = line[limit:]

        if len(current_chunk) + len(line) + 1 > limit:
            await interaction.followup.send(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        await interaction.followup.send(current_chunk)

@bot.tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    """Basic test command - measures connection latency."""
    COMMAND_REQUESTS.labels(command_name="ping").inc()
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(
        f"Pong! Latency: **{latency_ms}ms**"
    )

@bot.tree.command(name="ask", description="Ask the bot about PSO2 or PSO2:NGS")
@app_commands.describe(
    game="Choose the game version to search",
    question="Your question about the game"
)
@app_commands.rename(game="game", question="question")
async def ask(interaction: discord.Interaction, game: GameVersion, question: str):
    """Search the wiki database filtered by game version."""
    COMMAND_REQUESTS.labels(command_name="ask").inc()
    await interaction.response.defer(thinking=True)
    
    session_id = str(interaction.user.id)
    game_label = "PSO2 Classic" if game == GameVersion.PSO2 else "PSO2: New Genesis"
    game_key = "ngs" if game == GameVersion.NGS else "pso2"

    # RAG → MediaWiki API → model knowledge fallback chain
    extra = await bot._retrieve_wiki_context(question, game_key, game_label)
    reply = await bot.chat_agent.generate_reply(session_id, question, extra_context=extra)
    reply += bot.build_debug_suffix(flow_name="slash_ask", intent="wiki_search")
    
    full_reply = f"**[{game_label}]**\n{reply}"
    await send_long_message(interaction, full_reply)

@bot.tree.command(name="fashion", description="Identify a PSO2/NGS outfit from an image")
@app_commands.describe(
    game="Choose the game version to match fashion items",
    image="Upload a screenshot of the outfit"
)
@app_commands.rename(game="game", image="image")
async def fashion(interaction: discord.Interaction, game: GameVersion, image: discord.Attachment):
    """Analyze an uploaded image and search the fashion database."""
    COMMAND_REQUESTS.labels(command_name="fashion").inc()
    await interaction.response.defer(thinking=True)
    
    session_id = str(interaction.user.id)
    game_label = "PSO2 Classic" if game == GameVersion.PSO2 else "PSO2: New Genesis"
    
    if not image.content_type or 'image' not in image.content_type:
        await interaction.followup.send("Please upload a valid image file (PNG, JPG, etc.).")
        return
    
    img_bytes = await image.read()
    
    # 1. Vision Agent: Extract structural tags
    tags_str = await bot.vision.analyze_outfit_from_bytes(img_bytes)
    
    # 2. Chat Agent: Synthesize reply from vision-only context
    sys_msg = (
        f"[System Context] User uploaded an outfit image for {game_label}. "
        f"Vision analysis returned: {tags_str}. "
        "Provide a concise English-only description and practical search keywords. "
        f"Mention the game version: {game_label}."
    )
    reply = await bot.chat_agent.generate_reply(session_id, f"Identify this {game_label} outfit", extra_context=sys_msg)
    reply += bot.build_debug_suffix(flow_name="slash_fashion", intent="fashion_match")
    
    await interaction.followup.send(f"**[{game_label} Fashion]**\n{reply}")

@bot.tree.command(name="wiki", description="Fetch a PSO2/NGS wiki page and summarise it")
@app_commands.describe(
    url="Full URL of the wiki page to fetch",
    question="Optional question to answer from the page"
)
async def wiki(interaction: discord.Interaction, url: str, question: str = ""):
    """Fetch a wiki page via MCP and return an AI-summarised answer."""
    COMMAND_REQUESTS.labels(command_name="wiki").inc()
    await interaction.response.defer(thinking=True)

    if not bot.mcp:
        await interaction.followup.send("❌ MCP is disabled. Restart the bot with `--mcp` to enable wiki fetching.")
        return

    try:
        page_content = await bot.mcp.fetch_url(url)
    except ValueError as e:
        await interaction.followup.send(f"❌ {e}")
        return
    except RuntimeError as e:
        EXTERNAL_API_ERRORS.labels(service_name="mcp_fetch").inc()
        await interaction.followup.send(f"❌ Failed to fetch page: {e}")
        return

    session_id = str(interaction.user.id)
    user_prompt = question if question else f"Summarise this wiki page: {url}"

    extra_context = (
        "--- Live Wiki Data ---\n"
        "IMPORTANT: Base your answer ONLY on the following wiki content.\n"
        f"Source: {url}\n\n"
        f"{page_content}\n"
    )

    reply = await bot.chat_agent.generate_reply(session_id, user_prompt, extra_context=extra_context)
    reply += bot.build_debug_suffix(flow_name="slash_wiki", intent="wiki_search")

    await send_long_message(interaction, f"**[Wiki]** <{url}>\n{reply}")


if __name__ == "__main__":
    if not config.DISCORD_BOT_TOKEN:
        print("[ERROR] Missing DISCORD_BOT_TOKEN in .env file")
    else:
        print("[...] Starting PSO2 Bot...")
        bot.run(config.DISCORD_BOT_TOKEN)

