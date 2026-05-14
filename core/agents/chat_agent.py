"""
Chat Agent - The main conversational brain.
Synthesizes context (Memory, RAG, Vision) into natural replies using Groq LLM.
"""

import sys
import re
import json
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from groq import AsyncGroq
from settings import env as config
from core.memory import MemoryManager

def load_persona() -> str:
    """Load the default System Prompt persona (Matoi)"""
    try:
        path = PROJECT_ROOT / "ai_prompts" / "characters" / "default.md"
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception: # pylint: disable=broad-exception-caught
        return "You are an AI assistant for Phantasy Star Online 2."

def _load_formatting_prompt() -> str:
    """Load the structured-output formatting system prompt."""
    try:
        path = PROJECT_ROOT / "ai_prompts" / "formatting_system.md"
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:  # pylint: disable=broad-exception-caught
        return ""

class ChatAgent:
    """Handles generating the final responses using Groq API."""
    def __init__(self, memory_manager: MemoryManager):
        self.client = AsyncGroq(api_key=config.GROQ_API_KEY)
        self.model = config.GROQ_MODEL
        self.memory = memory_manager
        self.persona = load_persona()
        self._formatting_prompt = _load_formatting_prompt()
        self.last_debug_snapshot: dict = {}

    @staticmethod
    def _simple_greeting_reply(message: str) -> str | None:
        """Return a deterministic non-game-version-specific reply for simple greetings."""
        cleaned = re.sub(r"[^\w\s']", " ", message.lower()).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned in {
            "hi", "hello", "hey", "yo", "sup", "good morning",
            "good afternoon", "good evening", "hiya", "hey there",
        }:
            return "Hello. How can I help you with Phantasy Star Online 2 today?"
        return None

    async def _llm_completion(self, messages: list[dict]):
        """Wrapper to call chat completion with consistent defaults."""
        return await self.client.chat.completions.create(
            messages=messages,
            model=self.model,
            temperature=0.7,
            max_tokens=1024,
        )

    async def _build_system_prompt(self, user_id: str, memory_doc: dict, extra_context: str = "") -> str:
        """Combine Persona, Long-term Memory, and Extra Context into a single System Prompt."""
        prompt = self.persona + "\n\n"
        prompt += (
            "LANGUAGE POLICY: Always reply in English only. "
            "Do not use Vietnamese or any other language unless the user explicitly asks to switch language.\n\n"
        )
        prompt += (
            "GAME NAME POLICY: For greetings and general chat, refer to the game only as "
            "'Phantasy Star Online 2'. Do not say 'New Genesis', 'NGS', or 'Base' unless the user "
            "explicitly asks about that game version or retrieved wiki context specifies it.\n\n"
        )
        if not extra_context:
            prompt += (
                "FACTUAL GAME DATA POLICY: No retrieved wiki data is available in this turn. "
                "For factual Base or NGS questions about classes, skills, weapons, "
                "items, stats, prices, quests, builds, or mechanics, do not answer from general model "
                "knowledge and do not invent names or numbers. Say that you need to look it up in the "
                "ARKS database and ask the user to use /ask or rephrase as a specific wiki question.\n\n"
            )
        
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
            evidence_warning = ""
            if "[INSUFFICIENT_EVIDENCE]" in extra_context:
                evidence_warning = (
                    "EVIDENCE MODE: Retrieved evidence is weak or incomplete. "
                    "Do not guess. If evidence is insufficient, say so clearly and ask for a narrower query. "
                    "Only state facts that can be traced to sources in the retrieved data.\n"
                )

            prompt += (
                "--- Relevant Retrieved Data ---\n"
                "IMPORTANT: The following context is extracted from the Wiki. "
                "Base your answer ONLY on this context and the specified Game Version. "
                "Do NOT mix mechanics between Base and NGS (e.g., NGS Rangers cannot use Technics).\n"
                "Do NOT fabricate skill names, stat values, or mechanics that are not in the retrieved data.\n"
                "If the retrieved data does not contain the answer, say 'Data not found in the ARKS database.'\n"
                "If sources are present, cite at least one source URL in your final answer.\n"
                f"{evidence_warning}"
                f"{extra_context}\n\n"
            )
            
        return prompt

    async def generate_reply(self, user_id: str, message: str, extra_context: str = "") -> str:
        """Generate conversational reply based on History + Context."""
        debug_info = {
            "user_id": user_id,
            "insufficient_evidence": "[INSUFFICIENT_EVIDENCE]" in extra_context,
            "llm_mode": "direct",
        }

        quality_match = re.search(r"\[RETRIEVAL_QUALITY\]\s*([^\n]+)", extra_context)
        if quality_match:
            debug_info["retrieval_quality"] = quality_match.group(1).strip()
        
        # 1. Save user message to memory
        await self.memory.add_message(user_id, role="user", content=message)

        simple_greeting = self._simple_greeting_reply(message)
        if simple_greeting and not extra_context:
            debug_info["short_circuit_reason"] = "simple_greeting"
            debug_info["reply_len"] = len(simple_greeting)
            self.last_debug_snapshot = debug_info
            await self.memory.add_message(user_id, role="assistant", content=simple_greeting)
            return simple_greeting
        
        # Fetch memory payload
        memory_doc = await self.memory.get_memory(user_id)
        
        # 2. Build Chat payload
        system_prompt = await self._build_system_prompt(user_id, memory_doc, extra_context)
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # 3. Append short term history (filter out hallucinated messages)
        _HALLUCINATION_MARKERS = [
            "Data may be incomplete due to limited information",
            "I recommend checking the official PSO2 website",
        ]
        recent_history = memory_doc.get("recent_messages", [])[-5:]
        for msg in recent_history:
            role = msg.get("role")
            if role not in {"user", "assistant"}:
                continue
            if extra_context and role == "assistant":
                continue
            if msg["role"] == "assistant" and any(m in msg.get("content", "") for m in _HALLUCINATION_MARKERS):
                continue  # Skip likely-hallucinated responses
            messages.append({"role": role, "content": msg["content"]})
            
        # 5. Guard: if retrieval is explicitly low-confidence, avoid speculative generation.
        if "[INSUFFICIENT_EVIDENCE]" in extra_context:
            reply = (
                "I could not find enough reliable evidence to answer this accurately. "
                "Please provide a more specific class, skill, or weapon name and specify the game mode (Base or NGS)."
            )
            debug_info["short_circuit_reason"] = "insufficient_evidence"
            debug_info["reply_len"] = len(reply)
            self.last_debug_snapshot = debug_info
            await self.memory.add_message(user_id, role="assistant", content=reply)
            return reply

        # 6. Call API (vanilla LLM only)
        try:
            chat_completion = await self._llm_completion(messages)
            reply = chat_completion.choices[0].message.content or "*Silence*"
        except Exception as e: # pylint: disable=broad-exception-caught
            reply = f"System Error: Cannot connect to Groq Brain. ({str(e)})"
            debug_info["error"] = str(e)
            
        debug_info["reply_len"] = len(reply)
        self.last_debug_snapshot = debug_info

        # 7. Save assistant reply to memory
        await self.memory.add_message(user_id, role="assistant", content=reply)
        return reply

    def get_last_debug_snapshot(self) -> dict:
        """Return latest debug snapshot for external logging."""
        return self.last_debug_snapshot or {}

    async def generate_structured_reply(
        self,
        user_id: str,
        message: str,
        extra_context: str = "",
        preferred_format: str | None = None,
    ) -> dict | None:
        """Generate a structured JSON reply suitable for ASCII rendering.

        Returns the parsed dict on success, or None if the LLM fails to
        produce valid structured output (caller should fall back to
        generate_reply).
        """
        if not self._formatting_prompt:
            return None

        # Save user message
        await self.memory.add_message(user_id, role="user", content=message)
        memory_doc = await self.memory.get_memory(user_id)

        # Build system prompt: formatting instructions + context
        system_parts = [self._formatting_prompt, ""]

        # User long-term context
        summary = memory_doc.get("summary", "")
        facts = memory_doc.get("facts", [])
        if summary or facts:
            system_parts.append(f"--- User Context ---\n{summary}")
            if facts:
                system_parts.append("Known Facts: " + ", ".join(facts))
            system_parts.append("")

        # Retrieved wiki data
        if extra_context:
            system_parts.append(
                "--- Retrieved Data ---\n"
                "Base your answer ONLY on this data. "
                "Do NOT fabricate skill names, stat values, or mechanics.\n"
                f"{extra_context}"
            )

        if preferred_format in {"table", "kv", "bullets"}:
            system_parts.append(
                f"FORMAT OVERRIDE: You MUST return format='{preferred_format}' "
                "unless the retrieved data is clearly insufficient."
            )

        system_prompt = "\n".join(system_parts)

        # Recent history (lightweight — only last 3 turns)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        recent = memory_doc.get("recent_messages", [])[-3:]
        for msg in recent:
            role = msg.get("role")
            if role != "user":
                continue
            messages.append({"role": role, "content": msg["content"]})

        try:
            resp = await self.client.chat.completions.create(
                messages=messages,
                model=self.model,
                temperature=0.3,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            raw = (resp.choices[0].message.content or "").strip()
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"[STRUCTURED] LLM call failed: {e}")
            return None

        # Parse and validate
        from core.formatters.discord_table import try_parse_structured

        payload = try_parse_structured(raw)
        if payload is None:
            print(f"[STRUCTURED] Invalid JSON from LLM (len={len(raw)})")
            return None

        # Save assistant reply (the takeaway serves as the conversational message)
        takeaway = payload.get("takeaway", "")
        await self.memory.add_message(
            user_id, role="assistant",
            content=takeaway or json.dumps(payload, ensure_ascii=False)[:300],
        )
        return payload
