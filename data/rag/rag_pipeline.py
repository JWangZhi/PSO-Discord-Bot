"""
RAG Pipeline V3.0 — Hybrid Search (Pinecone + MongoDB).

Retrieval flow:
1. Receive query + optional game_mode filter.
2. Embed query via Local Embedding (LM Studio).
3. Search Pinecone for semantic text chunks (with metadata filter).
4. Search MongoDB for structured table data (exact match).
5. Merge context and return to LLM for synthesis.
"""

import sys
import re
from pathlib import Path
from dataclasses import dataclass

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from pinecone import Pinecone

from settings import env as config
from settings import app as app_settings


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    """A chunk retrieved from Pinecone (semantic text)."""
    text: str
    source: str
    page: str
    heading: str
    score: float
    game_mode: str = ""
    category: str = ""


# ---------------------------------------------------------------------------
# RAG Pipeline
# ---------------------------------------------------------------------------

class RAGPipeline:
    """Retrieval-Augmented Generation via Pinecone semantic search.

    - Embedding: Local (LM Studio)
    - Vector DB: Pinecone Cloud (semantic chunks)
    - LLM: Called outside this pipeline
    """

    MIN_CHUNK_CONFIDENCE = app_settings.RAG_MIN_CHUNK_CONFIDENCE
    GENERIC_QUERY_TOKENS = {
        "ngs", "pso2", "new", "genesis", "class", "classes", "skill", "skills",
        "guide", "info", "information", "what", "how", "does", "is", "are",
    }

    def __init__(self):
        # Local Embedding client
        self._embed_client = OpenAI(
            base_url=config.LOCAL_EMBED_URL,
            api_key="lm-studio",
        )

        # Pinecone
        pc = Pinecone(api_key=config.PINECONE_API_KEY)
        self._index = pc.Index(app_settings.RAG_WIKI_INDEX_NAME)

    # ---- Embedding ----

    def embed_query(self, query: str) -> list[float]:
        """Convert text query to vector."""
        response = self._embed_client.embeddings.create(
            model=config.LOCAL_EMBED_MODEL,
            input=[query],
        )
        return response.data[0].embedding

    # ---- Pinecone Search (Semantic) ----

    def search_chunks(
        self,
        query: str,
        top_k: int = 5,
        game_mode: str | None = None,
    ) -> list[RetrievedChunk]:
        """Search Pinecone for most relevant text chunks."""
        query_vector = self.embed_query(query)

        # Build filter
        pc_filter = {}
        if game_mode:
            pc_filter["game_mode"] = {"$eq": game_mode.upper()}

        results = self._index.query(
            vector=query_vector,
            top_k=top_k,
            include_metadata=True,
            filter=pc_filter if pc_filter else None,
        )

        chunks = []
        for match in results.matches:
            meta = match.metadata or {}
            chunks.append(RetrievedChunk(
                text=meta.get("text", ""),
                source=meta.get("url", ""),
                page=meta.get("page_title", ""),
                heading=meta.get("section", ""),
                score=match.score,
                game_mode=meta.get("game_mode", ""),
                category=meta.get("category", ""),
            ))

        return chunks

    # ---- MongoDB Search (Structured) ----

    @staticmethod
    def _tokenize_query(query: str) -> list[str]:
        """Tokenize a query into normalized terms for chunk re-ranking."""
        return [t for t in re.findall(r"[a-zA-Z0-9_+\-]{2,}", query.lower()) if t]

    @classmethod
    def _specific_tokens(cls, query_tokens: list[str]) -> list[str]:
        """Keep tokens that carry entity intent (e.g., 'slayer', 'gunblade')."""
        return [t for t in query_tokens if t not in cls.GENERIC_QUERY_TOKENS and len(t) >= 3]

    # ---- Context Builder ----

    def retrieve_context(
        self,
        query: str,
        game_mode: str | None = None,
        top_k: int = 5,
    ) -> str:
        """Main entry — build retrieval context from Pinecone with evidence quality markers."""

        # 1. Semantic search
        chunks = self.search_chunks(query, top_k=top_k, game_mode=game_mode)
        query_tokens = self._tokenize_query(query)
        specific_tokens = self._specific_tokens(query_tokens)

        # Re-rank chunks by boosting pages/text that contain specific tokens.
        if specific_tokens and chunks:
            boosted = []
            for c in chunks:
                haystack = f"{c.page} {c.heading} {c.text}".lower()
                hit_count = sum(1 for token in specific_tokens if token in haystack)
                boosted_score = c.score + (0.08 * hit_count)
                boosted.append((boosted_score, c))
            boosted.sort(key=lambda x: x[0], reverse=True)

            ranked_chunks = [c for _, c in boosted]
            matching_chunks = []
            non_matching_chunks = []
            for c in ranked_chunks:
                haystack = f"{c.page} {c.heading} {c.text}".lower()
                if any(token in haystack for token in specific_tokens):
                    matching_chunks.append(c)
                else:
                    non_matching_chunks.append(c)

            if len(matching_chunks) >= 2:
                chunks = matching_chunks[:top_k]
            elif matching_chunks:
                chunks = (matching_chunks + non_matching_chunks[:1])[:top_k]
            else:
                chunks = ranked_chunks[:top_k]

        # 2. Build context string
        parts = []
        chunk_max_score = max((c.score for c in chunks), default=0.0)
        source_urls = []

        if chunks:
            parts.append("=== Wiki Knowledge ===")
            for i, c in enumerate(chunks, 1):
                if c.source:
                    source_urls.append(c.source)
                parts.append(
                    f"--- Source {i}: {c.page} > {c.heading} "
                    f"[{c.game_mode}] (Score: {c.score:.2f}) ---\n"
                    f"{c.text}\n"
                )

        # Deduplicate sources while preserving order.
        unique_sources = []
        for url in source_urls:
            if url and url not in unique_sources:
                unique_sources.append(url)

        # Evidence gating marker for downstream response guard.
        strong_chunk = chunk_max_score >= self.MIN_CHUNK_CONFIDENCE
        specific_hit_in_chunks = True
        if specific_tokens and chunks:
            specific_hit_in_chunks = any(
                any(token in f"{c.page} {c.heading} {c.text}".lower() for token in specific_tokens)
                for c in chunks[:3]
            )

        insufficient = (
            not chunks
            or not strong_chunk
            or (specific_tokens and not specific_hit_in_chunks)
        )
        if insufficient:
            parts.insert(0, "[INSUFFICIENT_EVIDENCE]")

        parts.insert(0, f"[RETRIEVAL_QUALITY] chunk_max={chunk_max_score:.2f}")

        if unique_sources:
            parts.append("\n=== Sources ===")
            parts.extend(f"- {url}" for url in unique_sources[:8])

        if len(parts) <= 2 and insufficient:
            return "[INSUFFICIENT_EVIDENCE]\n[No relevant data found in Wiki.]"

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Quick Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Test RAG Pipeline (Pinecone) ===\n")
    rag = RAGPipeline()

    test_queries = [
        ("Hunter class skills", "NGS"),
        ("What weapon does Braver use?", "NGS"),
        ("Rivalate series stats", "PSO2"),
    ]

    for q, gm in test_queries:
        print(f"Q: {q} (game={gm})")
        print("-" * 50)

        chunks = rag.search_chunks(q, top_k=3, game_mode=gm)
        for c in chunks:
            print(f"  [CHUNK {c.score:.3f}] {c.page} > {c.heading}")
            print(f"    {c.text[:100]}...")

        print()
