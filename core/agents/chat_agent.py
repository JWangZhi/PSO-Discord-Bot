"""
Chat Agent - The main conversational brain.
Synthesizes context (Memory, RAG, Vision) into natural replies using Groq LLM.
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from groq import AsyncGroq
import config
from core.memory import MemoryManager

def load_persona() -> str:
    """Load the default System Prompt persona (Matoi)"""
    try:
        path = PROJECT_ROOT / "ai_prompts" / "characters" / "default.md"
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception: # pylint: disable=broad-exception-caught
        return "You are an AI assistant for Phantasy Star Online 2: New Genesis."

class ChatAgent:
    """Handles generating the final responses using Groq API."""
    def __init__(self, memory_manager: MemoryManager):
        self.client = AsyncGroq(api_key=config.GROQ_API_KEY)
        self.model = config.GROQ_MODEL
        self.memory = memory_manager
        self.persona = load_persona()

    async def _build_system_prompt(self, user_id: str, memory_doc: dict, extra_context: str = "") -> str:
        """Combine Persona, Long-term Memory, and Extra Context into a single System Prompt."""
        prompt = self.persona + "\n\n"
        
        # Inject long-term context
        summary = memory_doc.get("summary", "")
        facts = memory_doc.get("facts", [])
        
        long_term = summary
        if facts:
            long_term += "\nKnown Facts: " + ", ".join(facts)
            
        if long_term.strip():
            prompt += f"--- User Context (Known facts about User {user_id}) ---\n{long_term}\n\n"
            
        # Inject immediate extra context (e.g. Wiki data or Fashion match data)
        if extra_context:
            prompt += (
                "--- Relevant Retrieved Data ---\n"
                "IMPORTANT: The following context is extracted from the Wiki. "
                "Base your answer ONLY on this context and the specified Game Version. "
                "Do NOT mix mechanics between PSO2 Classic and NGS (e.g., NGS Rangers cannot use Technics).\n"
                f"{extra_context}\n\n"
            )
            
        return prompt

    async def generate_reply(self, user_id: str, message: str, extra_context: str = "") -> str:
        """Generate conversational reply based on History + Context."""
        
        # 1. Save user message to memory
        await self.memory.add_message(user_id, role="user", content=message)
        
        # Fetch memory payload
        memory_doc = await self.memory.get_memory(user_id)
        
        # 2. Build Chat payload
        system_prompt = await self._build_system_prompt(user_id, memory_doc, extra_context)
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # 3. Append short term history
        recent_history = memory_doc.get("recent_messages", [])[-5:]
        for msg in recent_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        # 5. Call API
        try:
            chat_completion = await self.client.chat.completions.create(
                messages=messages, # type: ignore
                model=self.model,
                temperature=0.7,
                max_tokens=1024
            )
            reply = chat_completion.choices[0].message.content or "*Silence*"
        except Exception as e: # pylint: disable=broad-exception-caught
            reply = f"System Error: Cannot connect to Groq Brain. ({str(e)})"
            
        # 6. Save assistant reply to memory
        await self.memory.add_message(user_id, role="assistant", content=reply)
        return reply
