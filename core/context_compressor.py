"""
Context Compressor

Uses Groq API (Llama 3) to summarize old messages in Short-term Memory,
updating the Long-term Summary, ensuring the Context window
remains small and cost-effective.
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import groq
from core.memory import MemoryManager
from settings import env as config

class ContextCompressor:
    """Handles automatic context compression using the Groq API."""

    def __init__(self):
        self.memory_manager = MemoryManager()
        self.client = groq.AsyncGroq(api_key=config.GROQ_API_KEY)
        self.threshold = config.COMPRESSION_THRESHOLD

    def should_compress(self, recent_messages: list[dict]) -> bool:
        """Check if compression is needed based on message count."""
        return len(recent_messages) > self.threshold

    async def compress(self, messages: list[dict], current_summary: str) -> str:
        """Summarize messages into a new summary using Groq."""
        if not messages:
            return current_summary

        # Format messages for the prompt
        chat_log = ""
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            chat_log += f"[{role}]: {msg['content']}\n"

        prompt = (
            "You are a Context Compression AI for a Phantasy Star Online 2 Discord Bot. "
            "Your task is to summarize the following chat log into a very brief, concise summary (under 100 words). "
            "Focus on the main events, user intent, emotional tone, and key facts. "
            "Do not include conversational filler. Respond ONLY with the summary text.\n\n"
        )
        if current_summary:
            prompt += f"Previous Summary: {current_summary}\n\n"
            
        prompt += f"Recent Chat Log to Compress:\n{chat_log}\n\nNew Summary:"

        try:
            response = await self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": prompt}
                ],
                model=config.GROQ_MODEL,
                max_tokens=200,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[Compressor] Error calling Groq API: {e}")
            return current_summary

    async def run_compression(self, channel_id: str) -> bool:
        """Run the full compression pipeline for a channel."""
        memory = await self.memory_manager.get_memory(channel_id)
        recent_messages = memory.get("recent_messages", [])
        
        if not self.should_compress(recent_messages):
            return False

        print(f"[Compressor] Triggering compression for channel {channel_id} "
              f"({len(recent_messages)} messages > {self.threshold})")

        # Keep the latest X messages
        keep_count = 6
        messages_to_compress = recent_messages[:-keep_count]
        messages_to_keep = recent_messages[-keep_count:]

        current_summary = memory.get("summary", "")
        
        # Determine new summary based on old messages
        new_summary = await self.compress(messages_to_compress, current_summary)

        # Update Long-term Memory
        await self.memory_manager.update_summary(channel_id, new_summary)
        
        # Manually truncate the recent_messages array in the DB
        await self.memory_manager._col.update_one(
            {"channel_id": channel_id},
            {"$set": {"recent_messages": messages_to_keep}}
        )
        
        print(f"[Compressor] Compression complete. New summary length: {len(new_summary)} chars.")
        return True


# --- Quick Test ---
if __name__ == "__main__":
    import asyncio
    from core.db import MongoDB

    async def test():
        print("=== Test Context Compressor ===")
        db = MongoDB.get_db()

        # Insert dummy data to test compression
        mm = MemoryManager()
        test_channel = "test_compress_001"
        
        # Clear old test data
        await mm.clear_memory(test_channel)
        
        print("Inserting 12 messages...")
        for i in range(12):
            role = "user" if i % 2 == 0 else "assistant"
            content = f"Message {i}: This is some placeholder test text about PSO2."
            await mm.add_message(test_channel, role, content)
            
        # Override DB directly for testing threshold
        await mm._col.update_one(
            {"channel_id": test_channel},
            {"$push": {
                "recent_messages": {
                    "$each": [
                        {"role": "user" if j % 2 == 0 else "assistant", 
                         "content": f"Message {j}: This is some placeholder test text about PSO2.", 
                         "timestamp": ""} for j in range(12)
                    ]
                }
            }}
        )

        compressor = ContextCompressor()
        
        mem_before = await mm.get_memory(test_channel)
        print(f"Messages before compression: {len(mem_before.get('recent_messages', []))}")
        
        await compressor.run_compression(test_channel)

        mem_after = await mm.get_memory(test_channel)
        print(f"Messages after compression: {len(mem_after.get('recent_messages', []))}")
        print(f"New summary: {mem_after.get('summary', '')}")
        
    asyncio.run(test())
