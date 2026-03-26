"""PSO2/NGS AI Advisor Bot - Entry Point"""

import time
import asyncio
import discord
from discord import app_commands

import config
from core.agents.router_agent import RouterAgent
from core.agents.chat_agent import ChatAgent
from core.agents.vision_agent import VisionAgent
from core.memory import MemoryManager
from data.rag.rag_pipeline import RAGPipeline
from data.rag.phashion_matcher import PhashionMatcher
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
        self.memory = MemoryManager()
        self.chat_agent = ChatAgent(self.memory)
        self.rag = RAGPipeline()
        self.vision = VisionAgent()
        self.phashion = PhashionMatcher()

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
                
                # Execute actual AI logic based on resolved Intent
                session_id = str(message.author.id)
                
                if result.intent == "fashion_match":
                    if has_image:
                        # Extract first image attachment
                        att = [a for a in message.attachments if a.content_type and 'image' in a.content_type][0]
                        img_bytes = await att.read()
                        
                        # 1. Vision Agent: Extract structural tags
                        tags_str = self.vision.analyze_outfit_from_bytes(img_bytes)
                        
                        # 2. Phashion Matcher: Search Pinecone
                        matches = self.phashion.find_matches(tags_str, top_k=3)
                        
                        if matches:
                            results_text = "Phashion matches found:\n" + "\n".join([f"- {m.item_name} (Score: {m.similarity_score:.2f})" for m in matches])
                        else:
                            results_text = "No matching Phashion items found in the database."
                            
                        # 3. Chat Agent: Synthesize final conversational reply
                        sys_msg = f"[System Context] User uploaded an image. You searched the database and found: {results_text}. Present this nicely."
                        reply = await self.chat_agent.generate_reply(session_id, content, extra_context=sys_msg)
                        await message.reply(reply)
                    else:
                        reply = await self.chat_agent.generate_reply(session_id, content, extra_context="[System Context] User asked for a fashion match but forgot to upload an image. Subtly remind them.")
                        await message.reply(reply)
                        
                elif result.intent == "wiki_search":
                    # 1. RAG Pipeline: Search local pinecone for factual Wiki data
                    rag_context = self.rag.retrieve_context(content)
                    # 2. Chat Agent: Answer using strictly the RAG Context
                    reply = await self.chat_agent.generate_reply(session_id, content, extra_context=rag_context)
                    await message.reply(reply)
                    
                else: # "chat"
                    # Default: Conversational memory utilizing the background Context Compressor natively
                    reply = await self.chat_agent.generate_reply(session_id, content)
                    await message.reply(reply)
                    
                # Record Metric: Processing Time
                MESSAGE_PROCESSING_TIME.labels(intent_type=result.intent).observe(time.time() - start_time)
                    
            except Exception as e:
                EXTERNAL_API_ERRORS.labels(service_name="router_llm").inc()
                await message.reply(f"❌ Error processing request: {e}")

bot = PSO2Bot()

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
    if len(text) <= 2000:
        await interaction.followup.send(text)
        return

    # Split by lines if possible to avoid cutting in mid-sentence
    lines = text.split("\n")
    current_chunk = ""
    
    for line in lines:
        if len(current_chunk) + len(line) + 1 > 2000:
            # Send current chunk and start new one
            await interaction.followup.send(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
            
    if current_chunk:
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
    
    # Add game context to the query for RAG filtering
    enriched_query = f"[Game: {game_label}] {question}"
    rag_context = bot.rag.retrieve_context(enriched_query, game_mode=game.value)
    
    # DEBUG: Log retrieved context to terminal
    print(f"\n{'='*60}")
    print(f"[DEBUG /ask] game={game.value}, question={question}")
    print(f"[DEBUG /ask] RAG context length: {len(rag_context)} chars")
    print(f"[DEBUG /ask] RAG context preview:\n{rag_context[:500]}")
    print(f"{'='*60}\n")
    
    extra = f"[System Context] User is asking about {game_label}. Use ONLY data relevant to {game_label}.\n{rag_context}"
    reply = await bot.chat_agent.generate_reply(session_id, question, extra_context=extra)
    
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
    tags_str = bot.vision.analyze_outfit_from_bytes(img_bytes)
    
    # 2. Phashion Matcher: Search Pinecone
    matches = bot.phashion.find_matches(tags_str, top_k=5)
    
    if matches:
        results_text = "\n".join([f"- **{m.item_name}** (Match: {m.similarity_score:.0%})" for m in matches])
    else:
        results_text = "No matching items found in the database."
    
    # 3. Chat Agent: Synthesize reply
    sys_msg = (
        f"[System Context] User uploaded an outfit image for {game_label}. "
        f"Vision analysis returned: {tags_str}. "
        f"Database search results:\n{results_text}\n"
        f"Present these results in a friendly way, mentioning the game version."
    )
    reply = await bot.chat_agent.generate_reply(session_id, f"Identify this {game_label} outfit", extra_context=sys_msg)
    
    await interaction.followup.send(f"**[{game_label} Fashion]**\n{reply}")

if __name__ == "__main__":
    if not config.DISCORD_BOT_TOKEN:
        print("[ERROR] Missing DISCORD_BOT_TOKEN in .env file")
    else:
        print("[...] Starting PSO2 Bot...")
        bot.run(config.DISCORD_BOT_TOKEN)

