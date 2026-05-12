"""PSO2/NGS AI Advisor Bot - Entry Point"""

import argparse
import time
import asyncio
import re
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
from core.wiki_search import WikiSearchService
from core.formatters.discord_table import render_response, try_parse_structured
from bot.cogs.rp_chat import RPChannelManager
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
parser.add_argument("--mcp", action="store_true", help="Enable MCP wiki fetching via MediaWiki API")
parser.add_argument("--debug", action="store_true", help="Enable debug logging to terminal")
cli_args = parser.parse_args()

# --debug flag overrides env-based settings
if cli_args.debug:
    import logging
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    # Suppress noisy library loggers
    for _noisy in ("pymongo", "httpcore", "httpx", "groq", "urllib3", "asyncio", "hpack"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    app_settings.BOT_DEBUG_ENABLED = True
    app_settings.BOT_DEBUG_INCLUDE_IN_REPLY = True

_VERSION_KEYWORDS_NGS = frozenset({
    "ngs", "new genesis", "retem", "kvaris", "stia", "halpha",
    "slayer", "waker", "stellar blade", "duel blade",
    "cocoon", "tower", "trinia", "aelio", "leciel",
    "augment", "add-on skill", "fixa",
})
_VERSION_KEYWORDS_PSO2 = frozenset({
    "pso2", "classic", "base game",
    "phantom", "hero", "etoile", "luster", "summoner",
    "premium set", "arks cash shop", "dark blast", "mag evolution",
    "matter board", "swap shop",
})


class GameVersionResolver:
    """Determines which game version (NGS vs PSO2) a wiki query targets.

    Priority:
      1. Hard keyword match in message text (unambiguous)
      2. Saved channel preference from memory
      3. Router suggestion (may be "ngs" by default)
      4. Ambiguous → return None to trigger clarification prompt
    """

    ASK_MSG = (
        "📋 Just to make sure I pull the right data — are you asking about "
        "**PSO2: New Genesis (NGS)** or **PSO2 Classic (Base)**?\n"
        "Reply with **NGS** or **PSO2** and I'll remember your preference for this channel."
    )
    SWITCH_MSG = (
        "🔄 It looks like you're switching from **{old}** to **{new}**. "
        "Should I start a fresh session for {new}? "
        "Reply **yes** to reset, or **no** to keep the current history."
    )
    VERSION_LABELS = {"ngs": "PSO2: New Genesis", "pso2": "PSO2 Classic"}

    def __init__(self, memory: "MemoryManager"):
        self._memory = memory

    def _hard_detect(self, text: str) -> str | None:
        """Return 'ngs'/'pso2' if the text contains unambiguous keywords, else None."""
        lower = text.lower()
        ngs_hit = any(k in lower for k in _VERSION_KEYWORDS_NGS)
        pso2_hit = any(k in lower for k in _VERSION_KEYWORDS_PSO2)
        if ngs_hit and not pso2_hit:
            return "ngs"
        if pso2_hit and not ngs_hit:
            return "pso2"
        return None  # ambiguous / both / neither

    def _parse_clarification(self, text: str) -> str | None:
        """Parse a user reply to the clarification prompt."""
        t = text.strip().lower()
        if t in {"ngs", "new genesis", "ngs!", "ngs."}:
            return "ngs"
        if t in {"pso2", "classic", "base", "base game", "pso2 classic", "pso2!"}:
            return "pso2"
        return None

    def _parse_switch_reply(self, text: str) -> bool | None:
        """Parse yes/no reply to the version-switch prompt. Returns True=reset, False=keep, None=unclear."""
        t = text.strip().lower()
        if t in {"yes", "y", "yeah", "yep", "sure", "ok", "reset", "new"}:
            return True
        if t in {"no", "n", "nope", "keep", "nah"}:
            return False
        return None

    async def resolve(
        self,
        session_id: str,
        message_text: str,
        router_suggestion: str,
    ) -> tuple[str | None, str | None]:
        """Resolve the game version for a wiki query.

        Returns:
            (version, pending_action)
            - version: "ngs" | "pso2" | None (None = must ask user first)
            - pending_action: "ask_version" | "ask_switch:{old}:{new}" | None
        """
        # 1. Hard keyword detection — highest priority
        hard = self._hard_detect(message_text)
        saved = await self._memory.get_game_version_pref(session_id)

        if hard:
            # Check if this is a version switch
            if saved and saved != hard:
                return None, f"ask_switch:{saved}:{hard}"
            # No conflict — save and use
            if not saved:
                await self._memory.set_game_version_pref(session_id, hard)
            return hard, None

        # 2. Use saved preference
        if saved:
            return saved, None

        # 3. Router gave a non-default signal (confident pso2 detection)
        if router_suggestion == "pso2":
            await self._memory.set_game_version_pref(session_id, "pso2")
            return "pso2", None

        # 4. Ambiguous — need to ask
        return None, "ask_version"


class PSO2Bot(discord.Client):
    """Main Discord Client for PSO2/NGS Bot."""

    _AMBIGUOUS_FOLLOWUP_PATTERNS = (
        r"\bit\b",
        r"\bthat\b",
        r"\bthis\b",
        r"\bthose\b",
        r"\bthem\b",
        r"\bmore details\b",
        r"\bmore about\b",
        r"\bthat skill\b",
        r"\bthis skill\b",
    )
    _STOPWORDS = {
        "tell", "me", "more", "about", "need", "detail", "details", "some", "number",
        "with", "of", "for", "the", "a", "an", "and", "please", "can", "you", "show",
        "what", "how", "is", "are", "does", "do", "on", "in", "to", "it", "that", "this",
        "skill", "skills", "class", "classes", "ngs", "pso2", "new", "genesis",
    }
    _KNOWN_CLASSES = {
        "hunter", "fighter", "ranger", "gunner", "force", "techter", "braver",
        "bouncer", "waker", "slayer", "summoner", "hero", "phantom", "etoile", "luster",
    }
    _STAT_TERMS = {
        "potency", "damage", "duration", "cooldown", "pp", "hp", "bp", "rate", "crit",
    }
    _COMPARE_HINTS = {"compare", "comparison", "difference", "different", "vs", "versus"}
    _BASE_HINTS = {"base", "classic", "pso2"}
    _NGS_HINTS = {"ngs", "new genesis"}
    _FACTUAL_QUERY_HINTS = {
        "skill", "skills", "class", "weapon", "weapons", "augment", "augments",
        "potency", "damage", "cooldown", "duration", "price", "cost", "premium",
        "set", "shop", "level", "quest", "where", "how", "what", "difference",
        "compare", "vs", "build", "units", "armor", "photon art", "pa",
    }

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
        self.wiki_search = WikiSearchService()
        self.rp_manager = RPChannelManager(MongoDB.get_db())
        self.gv_resolver = GameVersionResolver(self.memory)

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
            f"wiki_search=enabled",
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
        self, session_id: str, content: str, message: "discord.Message"
    ) -> bool:
        """Check if the user is replying to a clarification prompt (version or switch).

        Returns True if the message was handled as a clarification reply (caller should return).
        """
        mem = await self.memory.get_memory(session_id)
        recent = mem.get("recent_messages", [])

        # Scan last 3 system messages for pending markers
        pending_query: str | None = None
        pending_switch: tuple[str, str, str] | None = None  # (old, new, original_query)

        for msg in reversed(recent[-6:]):
            if msg.get("role") != "system":
                continue
            c = msg.get("content", "")
            if c.startswith("[PENDING_WIKI_QUERY]"):
                pending_query = c[len("[PENDING_WIKI_QUERY]"):].strip()
                break
            if c.startswith("[PENDING_SWITCH]"):
                # Parse: old=ngs new=pso2 query=...
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

        # Handle version clarification reply
        if pending_query is not None:
            version = self.gv_resolver._parse_clarification(content)
            if version is None:
                # User replied something unrelated — let normal flow handle it
                return False
            await self.memory.set_game_version_pref(session_id, version)
            label = GameVersionResolver.VERSION_LABELS[version]
            # Remove the system pending marker by re-saving without it
            cleaned = [m for m in recent if not m.get("content", "").startswith("[PENDING_WIKI_QUERY]")]
            await self.memory._col.update_one(
                {"channel_id": session_id},
                {"$set": {"recent_messages": cleaned}},
            )
            # Now answer the original question
            search_query = await self._expand_query(session_id, pending_query)
            extra = await self._retrieve_wiki_context(search_query, version, label)
            reply = await self._wiki_reply(session_id, pending_query, extra)
            reply = f"✅ Got it! I'll use **{label}** as your default for this channel.\n\n" + reply
            await reply_long(message, reply)
            return True

        # Handle switch yes/no reply
        if pending_switch is not None:
            answer = self.gv_resolver._parse_switch_reply(content)
            if answer is None:
                return False
            old_ver, new_ver, original_query = pending_switch
            # Remove pending marker
            cleaned = [m for m in recent if not m.get("content", "").startswith("[PENDING_SWITCH]")]
            await self.memory._col.update_one(
                {"channel_id": session_id},
                {"$set": {"recent_messages": cleaned}},
            )
            if answer:
                # Reset memory and switch version
                await self.memory.clear_memory(session_id)
                await self.memory.set_game_version_pref(session_id, new_ver)
                label = GameVersionResolver.VERSION_LABELS[new_ver]
                await message.reply(f"🔄 Session reset! Switched to **{label}**.")
            else:
                # Keep current version, answer with old version
                new_ver = old_ver
                label = GameVersionResolver.VERSION_LABELS[new_ver]
                await message.reply(f"👍 Keeping **{label}** session.")
            # Answer the original question with resolved version
            if original_query:
                label = GameVersionResolver.VERSION_LABELS[new_ver]
                search_query = await self._expand_query(session_id, original_query)
                extra = await self._retrieve_wiki_context(search_query, new_ver, label)
                reply = await self._wiki_reply(session_id, original_query, extra)
                await reply_long(message, reply)
            return True

        return False

    async def _expand_query(self, session_id: str, raw_query: str) -> str:
        """Resolve pronouns in follow-up user queries using recent conversation context."""
        q = raw_query.strip()
        if not q:
            return raw_query

        # Only expand truly ambiguous follow-up queries.
        lower_q = q.lower()
        if not any(re.search(pat, lower_q) for pat in self._AMBIGUOUS_FOLLOWUP_PATTERNS):
            return raw_query

        recent = await self.memory.get_recent_messages(session_id, limit=4)
        if len(recent) < 2:
            return raw_query

        history_lines = []
        for msg in recent:
            role = "User" if msg.get("role") == "user" else "Bot"
            content = msg.get("content", "")
            if len(content) > 200:
                content = content[:200] + "..."
            history_lines.append(f"{role}: {content}")

        history_block = "\n".join(history_lines)
        prompt = (
            "Given the conversation and a new user message, rewrite the user message "
            "as a standalone wiki search query that resolves pronouns and references. "
            "CRITICAL: Preserve concrete entities exactly (class names, skill names, weapon names, stats). "
            "If the new message is already specific, return it unchanged. "
            "Output only the rewritten query.\n\n"
            f"Conversation:\n{history_block}\n\n"
            f"New message: {raw_query}\n\n"
            "Rewritten query:"
        )

        try:
            from google import genai

            client = genai.Client(api_key=config.GEMINI_API_KEY)
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"temperature": 0.0, "max_output_tokens": 120},
            )
            expanded = (response.text or "").strip().strip('"').strip("'")
            if expanded and len(expanded) > 5:
                def _important_tokens(text: str) -> set[str]:
                    tokens = {
                        t for t in re.findall(r"[a-zA-Z0-9_+\-]{3,}", text.lower())
                        if t not in self._STOPWORDS
                    }
                    return tokens

                raw_tokens = _important_tokens(raw_query)
                expanded_tokens = _important_tokens(expanded)
                raw_classes = {t for t in raw_tokens if t in self._KNOWN_CLASSES}
                expanded_classes = {t for t in expanded_tokens if t in self._KNOWN_CLASSES}

                # Reject rewrites that drop all important terms from the original query.
                shared_tokens = raw_tokens & expanded_tokens
                if raw_tokens and not shared_tokens:
                    print(f"[QUERY_EXPAND] Rejected weak rewrite: '{expanded}' (from '{raw_query}')")
                    return raw_query

                # If original query contains an explicit class, expanded query must keep it.
                if raw_classes and not (raw_classes & expanded_classes):
                    print(f"[QUERY_EXPAND] Rejected class-dropping rewrite: '{expanded}' (from '{raw_query}')")
                    return raw_query

                # Prevent over-compressed rewrites that lose too much signal.
                min_shared = 1 if len(raw_tokens) <= 2 else 2
                if len(shared_tokens) < min_shared:
                    print(f"[QUERY_EXPAND] Rejected over-compressed rewrite: '{expanded}' (from '{raw_query}')")
                    return raw_query

                if expanded != raw_query:
                    print(f"[QUERY_EXPAND] '{raw_query}' -> '{expanded}'")
                return expanded
        except Exception as e:
            print(f"[WARN] Query expansion failed, using raw query: {e}")

        return raw_query

    def _important_tokens(self, text: str) -> set[str]:
        """Extract non-trivial tokens used for rewrite safety checks."""
        return {
            t for t in re.findall(r"[a-zA-Z0-9_+\-]{3,}", text.lower())
            if t not in self._STOPWORDS
        }

    def _clean_query(self, text: str) -> str:
        """Strip stopwords/filler from query text, preserving word order."""
        words = re.findall(r"[a-zA-Z0-9_+\-'']+", text)
        kept = [w for w in words if w.lower() not in self._STOPWORDS and len(w) >= 2]
        return " ".join(kept) if kept else text

    def _is_cross_version_compare(self, text: str, game_version: str) -> bool:
        """Detect comparison intent between PSO2 Classic and NGS."""
        lower = text.lower()
        has_compare = any(h in lower for h in self._COMPARE_HINTS)
        has_base = any(h in lower for h in self._BASE_HINTS)
        has_ngs = any(h in lower for h in self._NGS_HINTS)

        # Explicit compare mentioning both sides
        if has_compare and has_base and has_ngs:
            return True

        # Implicit phrasing: "compare to base" while router/default context is NGS
        if has_compare and has_base and game_version == "ngs":
            return True

        # Symmetric implicit phrasing for PSO2 context
        if has_compare and has_ngs and game_version == "pso2":
            return True

        return False

    def _looks_like_wiki_query(self, text: str, has_image: bool) -> bool:
        """Heuristic fallback: detect factual game queries that should use wiki_search.

        This protects regular chat flow when router misclassifies factual questions as chat.
        """
        if has_image:
            return False

        lower = text.lower().strip()
        if not lower:
            return False

        has_question = "?" in lower or any(
            lower.startswith(prefix) for prefix in ("what", "how", "where", "which", "compare")
        )
        has_class = any(cls in lower for cls in self._KNOWN_CLASSES)
        has_hint = any(h in lower for h in self._FACTUAL_QUERY_HINTS)

        # Require either an explicit class entity, or question+factual-hints combo.
        return has_class or (has_question and has_hint)

    @staticmethod
    def _clip_context(text: str, max_chars: int = 3200) -> str:
        """Keep context under size limit when merging multiple game-mode contexts."""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 64] + "\n...[truncated for context budget]"

    async def _extract_retrieval_query(self, raw_query: str, game_version: str) -> str:
        """Use LLM to distill class/skill/stat entities for retrieval, with strict safety guards."""
        raw_tokens = self._important_tokens(raw_query)
        cleaned = self._clean_query(raw_query)

        # Skip LLM extraction for already-short/specific queries — return cleaned.
        raw_word_count = len(re.findall(r"[a-zA-Z0-9_+\-]{2,}", raw_query.lower()))
        if raw_word_count <= 5:
            if cleaned != raw_query:
                print(f"[QUERY_KEYWORDS] cleaned: '{raw_query}' -> '{cleaned}'")
            return cleaned

        prompt = (
            "You are a keyword extractor for PSO2/NGS wiki search.\n"
            "Given a user query, extract ONLY the important game terms: "
            "class names, skill names, weapon names, item names, and stat terms "
            "(potency, cooldown, duration, PP, HP, etc.).\n"
            "Rules:\n"
            "- Keep multi-word skill/item names intact (e.g. 'Blight Rounds', not just 'Blight')\n"
            "- Always keep class names if mentioned\n"
            "- Drop filler words (tell, me, about, show, list, etc.)\n"
            "- Return ONE line of 2-8 keyword terms, no explanation\n\n"
            "Examples:\n"
            "query: Tell me more about skill Blight Rounds of Ranger and some number with potency of it\n"
            "keywords: Ranger Blight Rounds potency\n\n"
            "query: List Potency % of Blights Rounds of Ranger class\n"
            "keywords: Ranger Blight Rounds potency\n\n"
            "query: show me all skills of Ranger class in NGS\n"
            "keywords: Ranger skills\n\n"
            "query: what weapon does braver use and how to level up fast\n"
            "keywords: Braver weapon level\n\n"
            "query: how much damage does Final Nemesis do for Hunter\n"
            "keywords: Hunter Final Nemesis damage\n\n"
            f"query: {raw_query}\n"
            "keywords:"
        )

        try:
            from google import genai

            client = genai.Client(api_key=config.GEMINI_API_KEY)
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"temperature": 0.0, "max_output_tokens": 60},
            )
            extracted = (response.text or "").strip().strip('"').strip("'")
            if not extracted or len(extracted) < 3:
                print(f"[QUERY_KEYWORDS] Empty extraction, using cleaned: '{cleaned}'")
                return cleaned

            extracted_tokens = self._important_tokens(extracted)
            shared = raw_tokens & extracted_tokens
            raw_classes = {t for t in raw_tokens if t in self._KNOWN_CLASSES}
            extracted_classes = {t for t in extracted_tokens if t in self._KNOWN_CLASSES}

            # Guard 1: keep at least some original signal.
            if len(shared) < 1:
                print(f"[QUERY_KEYWORDS] Rejected zero-overlap extraction: '{extracted}', using cleaned: '{cleaned}'")
                return cleaned

            # Guard 2: if query mentions class explicitly, extracted query must keep it.
            if raw_classes and not (raw_classes & extracted_classes):
                # Auto-fix: prepend the missing class name(s).
                fix = " ".join(sorted(raw_classes)) + " " + extracted
                print(f"[QUERY_KEYWORDS] Auto-fixed class-dropping: '{extracted}' -> '{fix}'")
                extracted = fix

            # Guard 3: preserve stat intent when requested.
            raw_stats = {t for t in raw_tokens if t in self._STAT_TERMS}
            extracted_stats = {t for t in extracted_tokens if t in self._STAT_TERMS}
            if raw_stats and not (raw_stats & extracted_stats):
                # Auto-fix: append missing stat terms.
                fix = extracted + " " + " ".join(sorted(raw_stats))
                print(f"[QUERY_KEYWORDS] Auto-fixed stat-dropping: '{extracted}' -> '{fix}'")
                extracted = fix

            if extracted != raw_query:
                print(f"[QUERY_KEYWORDS] '{raw_query}' -> '{extracted}'")
            return extracted
        except Exception as e:
            print(f"[WARN] Query keyword extraction failed, using cleaned: {e}")
            return cleaned

    async def _retrieve_wiki_context(self, query: str, game_version: str, game_label: str) -> str:
        """Retrieve wiki context via MongoDB → MCP (live wiki) → model knowledge fallback chain."""
        # Compare mode: pull both PSO2 Classic and NGS evidence to avoid one-sided hallucinations.
        if self._is_cross_version_compare(query, game_version):
            retrieval_query = await self._extract_retrieval_query(query, game_version)
            sections: list[str] = [
                "[COMPARE_MODE]",
                "User asks for Base vs NGS comparison. Use BOTH evidence blocks below only.",
            ]

            pso2_ctx = None
            ngs_ctx = None
            try:
                pso2_ctx = await self.wiki_search.search(retrieval_query, "pso2")
            except Exception as e:
                print(f"[WARN] Compare mode PSO2 retrieval failed: {e}")
            try:
                ngs_ctx = await self.wiki_search.search(retrieval_query, "ngs")
            except Exception as e:
                print(f"[WARN] Compare mode NGS retrieval failed: {e}")

            if pso2_ctx:
                sections.append("\n--- PSO2 Classic Evidence ---\n" + self._clip_context(pso2_ctx))
            else:
                sections.append("\n[INSUFFICIENT_EVIDENCE] Missing PSO2 Classic evidence.")

            if ngs_ctx:
                sections.append("\n--- PSO2: New Genesis Evidence ---\n" + self._clip_context(ngs_ctx))
            else:
                sections.append("\n[INSUFFICIENT_EVIDENCE] Missing NGS evidence.")

            if not pso2_ctx and not ngs_ctx:
                return self.build_no_rag_context("PSO2 Classic and PSO2: New Genesis")

            return "\n".join(sections)

        retrieval_query = await self._extract_retrieval_query(query, game_version)

        # Entity-game validation (e.g., Phantom asked in NGS → redirect to PSO2)
        from core.mcp.mcp_client import validate_entity_game
        corrected_version, correction_warning = validate_entity_game(retrieval_query, game_version)
        if correction_warning:
            game_version = corrected_version
            game_label = "PSO2 Classic" if game_version == "pso2" else "PSO2: New Genesis"

        prefix = correction_warning or ""

        # 1. MongoDB wiki search (slug resolution + text search)
        try:
            mongo_ctx = await self.wiki_search.search(retrieval_query, game_version)
            if mongo_ctx:
                return prefix + mongo_ctx
        except Exception as e:
            print(f"[WARN] MongoDB wiki search failed: {e}")

        # 2. Fallback: Live wiki fetch via MediaWiki API
        if self.mcp:
            wiki_content = await self.mcp.search_wiki(retrieval_query, game_version=game_version)
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

    def _is_table_suitable(self, question: str) -> bool:
        """Heuristic: decide if a question should use structured table format.
        
        Returns True for comparison/list/stat queries suitable for tables/kv.
        Returns False for explanatory/conceptual questions that need free-text.
        """
        lower = question.lower()
        
        # TABLE-SUITABLE patterns: comparisons, lists, stat lookups, builds
        table_keywords = {
            "compare", "vs ", "versus", "difference", "different",
            "list", "all", "options", "which", "best", "better", "worse",
            "stats", "potency", "cooldown", "duration", "pp cost", "pp consumption",
            "build", "loadout", "setup", "equipped", "gear",
            "rank", "tier", "level", "damage", "defense", "hp", "attack",
            "cost", "price", "ac", "meseta", "exchange",
            "table", "data", "values", "numbers",
            "skill tree", "augment path", "progression",
        }
        
        # FREE-TEXT-ONLY patterns: explanations, mechanics, lore
        free_text_keywords = {
            "how to", "how do", "explain", "what is", "what does", 
            "why", "when", "tell me", "describe", "help", "guide",
            "lore", "story", "background", "history", "tip", "advice",
            "strategy", "playstyle", "meta", "viable", "worth",
            "farming", "grind", "unlock", "quest", "mission", "event",
            "mechanics", "system", "feature", "work", "work out",
        }
        
        # Strong free-text signals (override table signals)
        if any(k in lower for k in free_text_keywords):
            print(f"[TABLE_CHECK] Free-text pattern detected in: {question}")
            return False
        
        # Check for table-suitable signals
        if any(k in lower for k in table_keywords):
            print(f"[TABLE_CHECK] Table-suitable pattern detected in: {question}")
            return True
        
        # Default: if unknown structure, use free-text (safer default)
        print(f"[TABLE_CHECK] Ambiguous pattern, defaulting to free-text: {question}")
        return False

    async def _wiki_reply(self, session_id: str, question: str, extra_context: str) -> str:
        """Intelligently choose structured (table) vs free-text response.
        
        1. If insufficient evidence → always free-text
        2. If question is table-suitable → try structured, fallback to free-text
        3. Otherwise → go straight to free-text (safer, more natural)
        """
        # Skip structured path when evidence is insufficient
        if "[INSUFFICIENT_EVIDENCE]" in extra_context:
            print("[WIKI_REPLY] Insufficient evidence detected, using free-text")
            return await self.chat_agent.generate_reply(
                session_id, question, extra_context=extra_context,
            )
        
        # Check if this question is suitable for table format
        if not self._is_table_suitable(question):
            print("[WIKI_REPLY] Question is not table-suitable, using free-text directly")
            return await self.chat_agent.generate_reply(
                session_id, question, extra_context=extra_context,
            )
        
        # Try structured format for table-suitable questions
        payload = await self.chat_agent.generate_structured_reply(
            session_id, question, extra_context=extra_context,
        )
        if payload is not None:
            rendered = render_response(payload)
            print(f"[WIKI_REPLY] Structured reply success, format={payload.get('format')}")
            return rendered

        # Fallback: structured failed or didn't produce valid JSON → free-text
        print("[WIKI_REPLY] Structured reply failed, falling back to free-text")
        return await self.chat_agent.generate_reply(
            session_id, question, extra_context=extra_context,
        )

    async def setup_hook(self):
        """Sync Slash Commands on bot startup."""
        # Database indexes
        try:
            await MongoDB.ensure_indexes()
            print("[OK] MongoDB indexes ensured.")
        except Exception as e:
            print(f"[WARN] MongoDB index setup failed (bot continues): {e}")

        await self.rp_manager.load_all()
        print("[OK] RP channel config loaded.")

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

        # Check if this is a designated RP channel
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
            # Normal mode: only respond if the bot is mentioned
            if self.user not in message.mentions:
                return
            # Clean the message content (remove the bot mention)
            content = message.content.replace(f'<@{self.user.id}>', '').strip()
            session_id = str(message.author.id)

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
                if is_rp:
                    # RP channels: pure roleplay chat, skip intent routing
                    INTENT_REQUESTS.labels(intent_type="chat").inc()
                    reply = await self.chat_agent.generate_reply(session_id, content)
                    reply += self.build_debug_suffix(flow_name="rp_channel", intent="chat")
                    await reply_long(message, reply)
                    MESSAGE_PROCESSING_TIME.labels(intent_type="chat").observe(time.time() - start_time)
                    asyncio.create_task(self.compressor.run_compression(session_id))
                    return

                # LLM call is synchronous, so wrap in to_thread to avoid blocking discord.py loop
                result = await asyncio.to_thread(self.router.classify_intent, content, has_image)

                # --- Handle pending clarification replies ---
                pending_handled = await self._handle_pending_reply(session_id, content, message)
                if pending_handled:
                    return

                intent = result.intent
                if intent == "chat" and self._looks_like_wiki_query(content, has_image):
                    print("[ROUTER_FALLBACK] chat -> wiki_search (factual query heuristic)")
                    intent = "wiki_search"

                # Record Metric: Intent resolved
                INTENT_REQUESTS.labels(intent_type=intent).inc()

                # Execute actual AI logic based on resolved Intent
                
                if intent == "fashion_match":
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
                        await reply_long(message, reply)
                    else:
                        reply = await self.chat_agent.generate_reply(session_id, content, extra_context="[System Context] User asked for a fashion match but forgot to upload an image. Subtly remind them.")
                        await reply_long(message, reply)
                        
                elif intent == "wiki_search":
                    # --- Game version resolution ---
                    game_key, pending = await self.gv_resolver.resolve(
                        session_id, content, result.game_version
                    )

                    if pending == "ask_version":
                        # Store the original question so we can answer it after user replies
                        await self.memory.add_message(
                            session_id, "system",
                            f"[PENDING_WIKI_QUERY] {content}",
                        )
                        await message.reply(GameVersionResolver.ASK_MSG)
                        return

                    if pending and pending.startswith("ask_switch:"):
                        _, old_ver, new_ver = pending.split(":")
                        old_label = GameVersionResolver.VERSION_LABELS[old_ver]
                        new_label = GameVersionResolver.VERSION_LABELS[new_ver]
                        await self.memory.add_message(
                            session_id, "system",
                            f"[PENDING_SWITCH] old={old_ver} new={new_ver} query={content}",
                        )
                        await message.reply(
                            GameVersionResolver.SWITCH_MSG.format(old=old_label, new=new_label)
                        )
                        return

                    # Normal wiki search with resolved version
                    game_label = "PSO2 Classic" if game_key == "pso2" else "PSO2: New Genesis"
                    search_query = await self._expand_query(session_id, content)
                    extra = await self._retrieve_wiki_context(search_query, game_key, game_label)
                    reply = await self._wiki_reply(session_id, content, extra)
                    reply += self.build_debug_suffix(flow_name="on_message", intent=intent)
                    await reply_long(message, reply)
                    
                else: # "chat"
                    # Default: Conversational memory utilizing the background Context Compressor natively
                    reply = await self.chat_agent.generate_reply(session_id, content)
                    reply += self.build_debug_suffix(flow_name="on_message", intent=intent)
                    await reply_long(message, reply)
                    
                # Record Metric: Processing Time
                MESSAGE_PROCESSING_TIME.labels(intent_type=intent).observe(time.time() - start_time)

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

async def reply_long(message: discord.Message, text: str):
    """Split a long reply into 2000-char chunks for message.reply()."""
    limit = app_settings.DISCORD_MESSAGE_LIMIT
    if len(text) <= limit:
        await message.reply(text)
        return

    lines = text.split("\n")
    current_chunk = ""
    first = True

    for line in lines:
        while len(line) > limit:
            if current_chunk:
                if first:
                    await message.reply(current_chunk)
                    first = False
                else:
                    await message.channel.send(current_chunk)
                current_chunk = ""
            if first:
                await message.reply(line[:limit])
                first = False
            else:
                await message.channel.send(line[:limit])
            line = line[limit:]

        if len(current_chunk) + len(line) + 1 > limit:
            if first:
                await message.reply(current_chunk)
                first = False
            else:
                await message.channel.send(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        if first:
            await message.reply(current_chunk)
        else:
            await message.channel.send(current_chunk)

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
    session_id = str(interaction.user.id)
    await bot.memory.clear_memory(session_id)
    await interaction.response.send_message(
        "Memory cleared! Starting fresh.", ephemeral=True
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
    search_query = await bot._expand_query(session_id, question)
    extra = await bot._retrieve_wiki_context(search_query, game_key, game_label)
    reply = await bot._wiki_reply(session_id, question, extra)
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


if __name__ == "__main__":
    if not config.DISCORD_BOT_TOKEN:
        print("[ERROR] Missing DISCORD_BOT_TOKEN in .env file")
    else:
        print("[...] Starting PSO2 Bot...")
        bot.run(config.DISCORD_BOT_TOKEN)

