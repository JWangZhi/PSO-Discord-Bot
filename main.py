"""PSO2/NGS AI Advisor Bot - Entry Point"""

import discord
from discord import app_commands

import config


class PSO2Bot(discord.Client):
    """Main Discord Client for PSO2/NGS Bot."""

    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        """Sync Slash Commands on bot startup."""
        await self.tree.sync()
        print("[OK] Slash commands synced.")

    async def on_ready(self):
        print(f"[OK] Bot is online: {self.user} (ID: {self.user.id})")
        print(f"[OK] Serving {len(self.guilds)} server(s).")


bot = PSO2Bot()


@bot.tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    """Basic test command - measures connection latency."""
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(
        f"Pong! Latency: **{latency_ms}ms**"
    )


if __name__ == "__main__":
    if not config.DISCORD_BOT_TOKEN:
        print("[ERROR] Missing DISCORD_BOT_TOKEN in .env file")
    else:
        print("[...] Starting PSO2 Bot...")
        bot.run(config.DISCORD_BOT_TOKEN)
