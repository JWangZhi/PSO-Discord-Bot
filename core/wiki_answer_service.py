"""Wiki retrieval and answer orchestration for PSO2 bot flows."""

from __future__ import annotations

from typing import Any

from core.formatters.discord_table import render_response
from core.mcp.mcp_client import validate_entity_game


class WikiAnswerService:
    """Retrieves wiki evidence and chooses the safest answer renderer."""

    def __init__(
        self,
        *,
        memory: Any,
        chat_agent: Any,
        wiki_search: Any,
        query: Any,
        direct_replies: Any,
        mcp: Any | None = None,
        game_label,
    ) -> None:
        self.memory = memory
        self.chat_agent = chat_agent
        self.wiki_search = wiki_search
        self.query = query
        self.direct_replies = direct_replies
        self.mcp = mcp
        self.game_label = game_label

    @staticmethod
    def build_no_rag_context(game_label: str) -> str:
        """System context for game-specific queries without retrieved evidence."""
        return (
            "[INSUFFICIENT_EVIDENCE]\n"
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

    @staticmethod
    def clip_context(text: str, max_chars: int = 3200) -> str:
        """Keep context under size limit when merging multiple game-mode contexts."""
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 64] + "\n...[truncated for context budget]"

    async def retrieve_context(self, query: str, game_version: str, game_label: str) -> str:
        """Retrieve wiki context via MongoDB, optional live wiki, then no-RAG fallback."""
        if self.query.is_cross_version_compare(query, game_version):
            return await self.retrieve_both_context(
                query,
                mode_line="User asks for Base vs NGS comparison. Use BOTH evidence blocks below only.",
            )

        retrieval_query = await self.query.extract_retrieval_query(query, game_version)

        corrected_version, correction_warning = validate_entity_game(retrieval_query, game_version)
        if correction_warning:
            game_version = corrected_version
            game_label = self.game_label(game_version)

        prefix = correction_warning or ""

        try:
            mongo_ctx = await self.wiki_search.search(retrieval_query, game_version)
            if mongo_ctx:
                return prefix + mongo_ctx
        except Exception as e:
            print(f"[WARN] MongoDB wiki search failed: {e}")

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

        return prefix + self.build_no_rag_context(game_label)

    async def retrieve_both_context(
        self,
        query: str,
        *,
        mode_line: str = "User chose Both. Compare Base and NGS using ONLY the evidence blocks below.",
    ) -> str:
        """Retrieve Base and NGS context for comparison answers."""
        retrieval_query = await self.query.extract_retrieval_query(query, "ngs")
        sections: list[str] = ["[COMPARE_MODE]", mode_line]

        pso2_ctx = None
        ngs_ctx = None
        try:
            pso2_ctx = await self.wiki_search.search(retrieval_query, "pso2")
        except Exception as e:
            print(f"[WARN] Both mode Base retrieval failed: {e}")
        try:
            ngs_ctx = await self.wiki_search.search(retrieval_query, "ngs")
        except Exception as e:
            print(f"[WARN] Both mode NGS retrieval failed: {e}")

        if pso2_ctx:
            pso2_ctx = pso2_ctx.replace("[INSUFFICIENT_EVIDENCE]", "[WEAK_EVIDENCE]")
            sections.append("\n--- Base Evidence ---\n" + self.clip_context(pso2_ctx))
        else:
            sections.append("\n[MISSING_EVIDENCE] Missing Base evidence.")

        if ngs_ctx:
            ngs_ctx = ngs_ctx.replace("[INSUFFICIENT_EVIDENCE]", "[WEAK_EVIDENCE]")
            sections.append("\n--- NGS Evidence ---\n" + self.clip_context(ngs_ctx))
        else:
            sections.append("\n[MISSING_EVIDENCE] Missing NGS evidence.")

        if not pso2_ctx and not ngs_ctx:
            return self.build_no_rag_context("Base and NGS")

        return "\n".join(sections)

    async def reply(self, session_id: str, question: str, extra_context: str) -> str:
        """Choose deterministic, structured, or free-text rendering for wiki answers."""
        if "[INSUFFICIENT_EVIDENCE]" in extra_context:
            print("[WIKI_REPLY] Insufficient evidence detected, using free-text")
            return await self.chat_agent.generate_reply(
                session_id, question, extra_context=extra_context,
            )

        direct_price_reply = self.direct_replies.direct_both_price_reply(question, extra_context)
        if direct_price_reply:
            await self.memory.add_message(session_id, role="user", content=question)
            await self.memory.add_message(session_id, role="assistant", content=direct_price_reply)
            print("[WIKI_REPLY] Direct Both-mode price reply from retrieved context")
            return direct_price_reply

        direct_reply = self.direct_replies.direct_class_overview_reply(question, extra_context)
        if direct_reply:
            await self.memory.add_message(session_id, role="user", content=question)
            await self.memory.add_message(session_id, role="assistant", content=direct_reply)
            print("[WIKI_REPLY] Direct class overview reply from retrieved context")
            return direct_reply

        if not self.query.is_table_suitable(question):
            print("[WIKI_REPLY] Question is not table-suitable, using free-text directly")
            return await self.chat_agent.generate_reply(
                session_id, question, extra_context=extra_context,
            )

        entities = self.query.format_entities(question)
        entity_count = len(entities)
        preferred_format = "kv" if entity_count == 1 else None
        print(
            f"[WIKI_REPLY] entity_count={entity_count} entities={sorted(entities)[:4]} "
            f"preferred_format={preferred_format or 'auto'}"
        )

        if entity_count == 0:
            print("[WIKI_REPLY] No clear entities, using free-text")
            return await self.chat_agent.generate_reply(
                session_id, question, extra_context=extra_context,
            )

        payload = await self.chat_agent.generate_structured_reply(
            session_id,
            question,
            extra_context=extra_context,
            preferred_format=preferred_format,
        )
        if payload is not None:
            rendered = render_response(payload)
            print(f"[WIKI_REPLY] Structured reply success, format={payload.get('format')}")
            return rendered

        print("[WIKI_REPLY] Structured reply failed, falling back to free-text")
        return await self.chat_agent.generate_reply(
            session_id, question, extra_context=extra_context,
        )
