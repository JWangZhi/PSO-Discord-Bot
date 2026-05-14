"""Query heuristics and keyword extraction for wiki retrieval."""

from __future__ import annotations

import re

from settings import env as config


AMBIGUOUS_FOLLOWUP_PATTERNS = (
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
STOPWORDS = {
    "tell", "me", "more", "about", "need", "detail", "details", "some", "number",
    "with", "of", "for", "the", "a", "an", "and", "please", "can", "you", "show",
    "what", "how", "is", "are", "does", "do", "on", "in", "to", "it", "that", "this",
    "skill", "skills", "class", "classes", "ngs", "pso2", "new", "genesis",
}
KNOWN_CLASSES = {
    "hunter", "fighter", "ranger", "gunner", "force", "techter", "braver",
    "bouncer", "waker", "slayer", "summoner", "hero", "phantom", "etoile", "luster",
}
STAT_TERMS = {
    "potency", "damage", "duration", "cooldown", "pp", "hp", "bp", "rate", "crit",
}
COMPARE_HINTS = {"compare", "comparison", "difference", "different", "vs", "versus"}
BASE_HINTS = {"base", "classic", "pso2"}
NGS_HINTS = {"ngs", "new genesis"}
FACTUAL_QUERY_HINTS = {
    "skill", "skills", "class", "weapon", "weapons", "augment", "augments",
    "potency", "damage", "cooldown", "duration", "price", "cost", "premium",
    "set", "shop", "level", "quest", "where", "how", "what", "difference",
    "compare", "vs", "build", "units", "armor", "photon art", "pa",
    "fixa", "add-on", "addon", "tech arts", "photon blast", "cocoon",
}
FACTUAL_ACTION_HINTS = {
    "tell", "show", "list", "explain", "describe", "guide", "build",
    "compare", "recommend", "find", "where", "what", "how", "which",
    "know", "learn", "want",
}


class WikiQueryProcessor:
    """Owns wiki-query classification, rewriting, and keyword extraction."""

    def __init__(self, memory) -> None:
        self.memory = memory

    async def expand_query(self, session_id: str, raw_query: str) -> str:
        """Resolve pronouns in follow-up user queries using recent conversation context."""
        q = raw_query.strip()
        if not q:
            return raw_query

        lower_q = q.lower()
        if not any(re.search(pat, lower_q) for pat in AMBIGUOUS_FOLLOWUP_PATTERNS):
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
                raw_tokens = self.important_tokens(raw_query)
                expanded_tokens = self.important_tokens(expanded)
                raw_classes = {t for t in raw_tokens if t in KNOWN_CLASSES}
                expanded_classes = {t for t in expanded_tokens if t in KNOWN_CLASSES}

                shared_tokens = raw_tokens & expanded_tokens
                if raw_tokens and not shared_tokens:
                    print(f"[QUERY_EXPAND] Rejected weak rewrite: '{expanded}' (from '{raw_query}')")
                    return raw_query

                if raw_classes and not (raw_classes & expanded_classes):
                    print(f"[QUERY_EXPAND] Rejected class-dropping rewrite: '{expanded}' (from '{raw_query}')")
                    return raw_query

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

    def important_tokens(self, text: str) -> set[str]:
        """Extract non-trivial tokens used for rewrite safety checks."""
        return {
            t for t in re.findall(r"[a-zA-Z0-9_+\-]{3,}", text.lower())
            if t not in STOPWORDS
        }

    def clean_query(self, text: str) -> str:
        """Strip stopwords/filler from query text, preserving word order."""
        words = re.findall(r"[a-zA-Z0-9_+\-'']+", text)
        kept = [
            w for w in words
            if w.lower() not in STOPWORDS and (len(w) >= 2 or w.isdigit())
        ]
        return " ".join(kept) if kept else text

    def is_cross_version_compare(self, text: str, game_version: str) -> bool:
        """Detect comparison intent between Base and NGS."""
        lower = text.lower()
        has_compare = any(h in lower for h in COMPARE_HINTS)
        has_base = any(h in lower for h in BASE_HINTS)
        has_ngs = any(h in lower for h in NGS_HINTS)

        if has_compare and has_base and has_ngs:
            return True
        if has_compare and has_base and game_version == "ngs":
            return True
        if has_compare and has_ngs and game_version == "pso2":
            return True
        return False

    def looks_like_wiki_query(self, text: str, has_image: bool) -> bool:
        """Heuristic fallback for factual game queries."""
        if has_image:
            return False

        lower = text.lower().strip()
        if not lower:
            return False

        has_question = "?" in lower or any(
            lower.startswith(prefix) for prefix in ("what", "how", "where", "which", "compare")
        )
        has_class = any(cls in lower for cls in KNOWN_CLASSES)
        has_hint = any(h in lower for h in FACTUAL_QUERY_HINTS)
        has_action = any(
            re.search(rf"\b{re.escape(h)}\b", lower)
            for h in FACTUAL_ACTION_HINTS
        )
        return has_class or (has_hint and (has_question or has_action))

    async def extract_retrieval_query(self, raw_query: str, game_version: str) -> str:
        """Use LLM to distill class/skill/stat entities for retrieval."""
        raw_tokens = self.important_tokens(raw_query)
        cleaned = self.clean_query(raw_query)

        raw_word_count = len(re.findall(r"[a-zA-Z0-9_+\-]{2,}", raw_query.lower()))
        if raw_word_count <= 5:
            raw_classes = {t for t in raw_tokens if t in KNOWN_CLASSES}
            if re.search(r"\bclass(?:es)?\b", raw_query.lower()) and raw_classes and "class" not in cleaned.lower():
                cleaned = f"{cleaned} class"
                print(f"[QUERY_KEYWORDS] Preserved class intent in short query: '{cleaned}'")
            if cleaned != raw_query:
                print(f"[QUERY_KEYWORDS] cleaned: '{raw_query}' -> '{cleaned}'")
            return cleaned

        prompt = (
            "You are a keyword extractor for Base/NGS wiki search.\n"
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

            extracted_tokens = self.important_tokens(extracted)
            shared = raw_tokens & extracted_tokens
            raw_classes = {t for t in raw_tokens if t in KNOWN_CLASSES}
            extracted_classes = {t for t in extracted_tokens if t in KNOWN_CLASSES}

            if len(shared) < 1:
                print(f"[QUERY_KEYWORDS] Rejected zero-overlap extraction: '{extracted}', using cleaned: '{cleaned}'")
                return cleaned

            if raw_classes and not (raw_classes & extracted_classes):
                fix = " ".join(sorted(raw_classes)) + " " + extracted
                print(f"[QUERY_KEYWORDS] Auto-fixed class-dropping: '{extracted}' -> '{fix}'")
                extracted = fix
                extracted_tokens = self.important_tokens(extracted)

            if re.search(r"\bclass(?:es)?\b", raw_query.lower()) and raw_classes:
                fix = f"{extracted} class"
                print(f"[QUERY_KEYWORDS] Preserved class intent: '{extracted}' -> '{fix}'")
                extracted = fix
                extracted_tokens = self.important_tokens(extracted)

            raw_stats = {t for t in raw_tokens if t in STAT_TERMS}
            extracted_stats = {t for t in extracted_tokens if t in STAT_TERMS}
            if raw_stats and not (raw_stats & extracted_stats):
                fix = extracted + " " + " ".join(sorted(raw_stats))
                print(f"[QUERY_KEYWORDS] Auto-fixed stat-dropping: '{extracted}' -> '{fix}'")
                extracted = fix

            if extracted != raw_query:
                print(f"[QUERY_KEYWORDS] '{raw_query}' -> '{extracted}'")
            return extracted
        except Exception as e:
            print(f"[WARN] Query keyword extraction failed, using cleaned: {e}")
            return cleaned

    def is_table_suitable(self, question: str) -> bool:
        """Decide if a question should use structured table/KV formatting."""
        lower = question.lower()
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
        free_text_keywords = {
            "how to", "how do", "explain", "what is", "what does",
            "why", "when", "tell me", "describe", "help", "guide",
            "lore", "story", "background", "history", "tip", "advice",
            "strategy", "playstyle", "meta", "viable", "worth",
            "farming", "grind", "unlock", "quest", "mission", "event",
            "mechanics", "system", "feature", "work", "work out",
        }

        if any(k in lower for k in free_text_keywords):
            print(f"[TABLE_CHECK] Free-text pattern detected in: {question}")
            return False
        if any(k in lower for k in table_keywords):
            print(f"[TABLE_CHECK] Table-suitable pattern detected in: {question}")
            return True

        print(f"[TABLE_CHECK] Ambiguous pattern, defaulting to free-text: {question}")
        return False

    def format_entities(self, question: str) -> set[str]:
        """Extract lightweight entities for response-format decisions."""
        lower = question.lower()
        entities: set[str] = set()

        for cls in KNOWN_CLASSES:
            if re.search(rf"\b{re.escape(cls)}\b", lower):
                entities.add(cls)

        if " vs " in lower or " versus " in lower:
            parts = re.split(r"\bvs\b|\bversus\b", lower)
            for part in parts:
                tokens = [
                    t for t in re.findall(r"[a-z0-9_+\-]{3,}", part)
                    if t not in STOPWORDS and t not in FACTUAL_QUERY_HINTS
                ]
                if tokens:
                    entities.add(" ".join(tokens[:2]))

        if not entities:
            tokens = [
                t for t in re.findall(r"[a-z0-9_+\-]{3,}", lower)
                if t not in STOPWORDS and t not in FACTUAL_QUERY_HINTS
            ]
            if tokens:
                entities.add(tokens[0])

        return entities
