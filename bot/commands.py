"""Slash command registration for the Discord bot."""

import discord
from discord import app_commands

from bot.client import PSO2Bot
from bot.discord_utils import send_long_message
from core.telemetry import COMMAND_REQUESTS, EXTERNAL_API_ERRORS


class GameVersion(discord.Enum):
    Base = "pso2"
    NGS = "ngs"


def register_commands(bot: PSO2Bot) -> None:
    """Register slash commands on the bot command tree."""

    @bot.tree.command(name="ping", description="Check if the bot is alive")
    async def ping(interaction: discord.Interaction):
        """Basic test command - measures connection latency."""
        COMMAND_REQUESTS.labels(command_name="ping").inc()
        latency_ms = round(bot.latency * 1000)
        await interaction.response.send_message(
            f"Pong! Latency: **{latency_ms}ms**"
        )

    @bot.tree.command(name="clear_memory", description="Clear conversation memory for your session")
    async def clear_memory(interaction: discord.Interaction):
        """Wipe stored memory so the bot starts fresh for this user."""
        COMMAND_REQUESTS.labels(command_name="clear_memory").inc()
        is_rp = (
            interaction.guild is not None
            and interaction.channel is not None
            and bot.rp_manager.is_rp_channel(interaction.guild.id, interaction.channel.id)
        )
        session_id = str(interaction.channel.id) if is_rp else str(interaction.user.id)
        await bot.memory.clear_memory(session_id)
        await interaction.response.send_message(
            "Channel memory cleared! Starting fresh." if is_rp else "Memory cleared! Starting fresh.",
            ephemeral=True,
        )

    @bot.tree.command(name="ask", description="Ask the bot about Base or NGS")
    @app_commands.describe(
        game="Choose the game version to search",
        question="Your question about the game",
    )
    @app_commands.rename(game="game", question="question")
    async def ask(interaction: discord.Interaction, game: GameVersion, question: str):
        """Search the wiki database filtered by game version."""
        COMMAND_REQUESTS.labels(command_name="ask").inc()
        await interaction.response.defer(thinking=True)

        session_id = str(interaction.user.id)
        game_key = game.value
        game_label = bot.game_label(game_key)

        search_query = await bot.query.expand_query(session_id, question)
        extra = await bot.wiki_answers.retrieve_context(search_query, game_key, game_label)
        reply = await bot.wiki_answers.reply(session_id, question, extra)
        reply += bot.build_debug_suffix(flow_name="slash_ask", intent="wiki_search")

        full_reply = f"**[{game_label}]**\n{reply}"
        await send_long_message(interaction, full_reply)

    @bot.tree.command(name="fashion", description="Identify a Base or NGS outfit from an image")
    @app_commands.describe(
        game="Choose the game version to match fashion items",
        image="Upload a screenshot of the outfit",
    )
    @app_commands.rename(game="game", image="image")
    async def fashion(interaction: discord.Interaction, game: GameVersion, image: discord.Attachment):
        """Analyze an uploaded image and search the fashion database."""
        COMMAND_REQUESTS.labels(command_name="fashion").inc()
        await interaction.response.defer(thinking=True)

        session_id = str(interaction.user.id)
        game_label = bot.game_label(game.value)

        if not image.content_type or "image" not in image.content_type:
            await interaction.followup.send("Please upload a valid image file (PNG, JPG, etc.).")
            return

        img_bytes = await image.read()
        tags_str = await bot.vision.analyze_outfit_from_bytes(img_bytes)
        sys_msg = (
            f"[System Context] User uploaded an outfit image for {game_label}. "
            f"Vision analysis returned: {tags_str}. "
            "Provide a concise English-only description and practical search keywords. "
            f"Mention the game version: {game_label}."
        )
        reply = await bot.chat_agent.generate_reply(
            session_id,
            f"Identify this {game_label} outfit",
            extra_context=sys_msg,
        )
        reply += bot.build_debug_suffix(flow_name="slash_fashion", intent="fashion_match")

        await interaction.followup.send(f"**[{game_label} Fashion]**\n{reply}")

    @bot.tree.command(name="wiki", description="Fetch a Base or NGS wiki page and summarise it")
    @app_commands.describe(
        url="Full URL of the wiki page to fetch",
        question="Optional question to answer from the page",
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

    @bot.tree.command(name="rp_enable", description="Enable roleplay mode in this channel (bot responds to everyone)")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def rp_enable(interaction: discord.Interaction):
        """Designate the current channel as an RP channel."""
        COMMAND_REQUESTS.labels(command_name="rp_enable").inc()
        await interaction.response.defer(ephemeral=True)
        await bot.rp_manager.enable(interaction.guild_id, interaction.channel_id)
        await interaction.followup.send(
            f"✅ **#{interaction.channel.name}** is now an RP channel.\n"
            "I'll respond to every message here without needing @mention.\n"
            "Use `/clear_memory` to reset my memory for this channel.",
            ephemeral=True,
        )

    @bot.tree.command(name="rp_disable", description="Disable roleplay mode in this channel")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def rp_disable(interaction: discord.Interaction):
        """Remove the current channel from RP mode."""
        COMMAND_REQUESTS.labels(command_name="rp_disable").inc()
        await interaction.response.defer(ephemeral=True)
        await bot.rp_manager.disable(interaction.guild_id, interaction.channel_id)
        await interaction.followup.send(
            f"✅ RP mode disabled for **#{interaction.channel.name}**.\n"
            "I'll only respond when @mentioned.",
            ephemeral=True,
        )
