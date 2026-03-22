"""PSO2/NGS AI Advisor Bot - Entry Point"""

import time
import asyncio
import discord
from discord import app_commands

import config
from core.agents.router_agent import RouterAgent
from core.telemetry import (
    start_metrics_server, 
    INTENT_REQUESTS, 
    MESSAGE_PROCESSING_TIME, 
    COMMAND_REQUESTS,
    EXTERNAL_API_ERRORS
)

class PSO2Bot(discord.Client):
    """Main Discord Client for PSO2/NGS Bot."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True # Required for reading messages
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.router = RouterAgent()

    async def setup_hook(self):
        """Sync Slash Commands on bot startup."""
        await self.tree.sync()
        print("[OK] Slash commands synced.")

    async def on_ready(self):
        print(f"[OK] Bot is online: {self.user} (ID: {self.user.id})")
        print(f"[OK] Serving {len(self.guilds)} server(s).")
        
        # Start Prometheus metrics server
        start_metrics_server(port=8000)
        
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
                
                # Temporary stubs for testing intent classification execution
                if result.intent == "fashion_match":
                    await message.reply(f"👗 **Fashion Intent Detected**\nConfidence: `{result.confidence}`\nReasoning: `{result.reasoning}`")
                elif result.intent == "wiki_search":
                    await message.reply(f"📚 **Wiki Search Intent Detected**\nConfidence: `{result.confidence}`\nReasoning: `{result.reasoning}`")
                else:
                    await message.reply(f"💬 **Chat/RP Intent Detected**\nConfidence: `{result.confidence}`\nReasoning: `{result.reasoning}`")
                    
                # Record Metric: Processing Time
                MESSAGE_PROCESSING_TIME.labels(intent_type=result.intent).observe(time.time() - start_time)
                    
            except Exception as e:
                EXTERNAL_API_ERRORS.labels(service_name="router_llm").inc()
                await message.reply(f"❌ Error processing request: {e}")

bot = PSO2Bot()

@bot.tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    """Basic test command - measures connection latency."""
    COMMAND_REQUESTS.labels(command_name="ping").inc()
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
