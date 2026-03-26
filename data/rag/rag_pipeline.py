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
from pathlib import Path
from dataclasses import dataclass

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from pinecone import Pinecone
from pymongo import MongoClient

import config


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


@dataclass
class TableResult:
    """A row retrieved from MongoDB (structured stats)."""
    data: dict
    page: str
    source_url: str
    game_mode: str = ""
    category: str = ""


# ---------------------------------------------------------------------------
# RAG Pipeline
# ---------------------------------------------------------------------------

class RAGPipeline:
    """Retrieval-Augmented Generation with Hybrid Search.

    - Embedding: Local (LM Studio)
    - Vector DB: Pinecone Cloud (semantic chunks)
    - Document DB: MongoDB Atlas (structured tables)
    - LLM: Called outside this pipeline
    """

    def __init__(self):
        # Local Embedding client
        self._embed_client = OpenAI(
            base_url=config.LOCAL_EMBED_URL,
            api_key="lm-studio",
        )

        # Pinecone
        pc = Pinecone(api_key=config.PINECONE_API_KEY)
        self._index = pc.Index("pso2-wiki")

        # MongoDB
        mongo_client = MongoClient(config.MONGODB_URI)
        self._table_coll = mongo_client["pso2_bot"]["wiki_tables"]

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

    def search_tables(
        self,
        query: str,
        game_mode: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[TableResult]:
        """Search MongoDB for structured table rows matching query terms."""
        mongo_filter: dict = {}

        if game_mode:
            mongo_filter["game_mode"] = game_mode.upper()
        if category:
            mongo_filter["category"] = category

        # Text search across nested 'data' fields
        # Simple approach: regex on stringified data values
        if query:
            mongo_filter["$or"] = [
                {f"data.{key}": {"$regex": query, "$options": "i"}}
                for key in ["name", "weapon", "series", "item",
                            "costume", "accessory", "hairstyle"]
            ]

        results = []
        cursor = self._table_coll.find(mongo_filter).limit(limit)

        for doc in cursor:
            results.append(TableResult(
                data=doc.get("data", {}),
                page=doc.get("page", ""),
                source_url=doc.get("source_url", ""),
                game_mode=doc.get("game_mode", ""),
                category=doc.get("category", ""),
            ))

        return results

    # ---- Hybrid Context Builder ----

    def retrieve_context(
        self,
        query: str,
        game_mode: str | None = None,
        top_k: int = 5,
    ) -> str:
        """Main entry — Parallel search, merge context for LLM prompt."""

        # 1. Semantic search (always)
        chunks = self.search_chunks(query, top_k=top_k, game_mode=game_mode)

        # 2. Table search (entity detection — search if query has specific terms)
        table_results = self.search_tables(query, game_mode=game_mode, limit=5)

        # 3. Build context string
        parts = []

        if chunks:
            parts.append("=== Wiki Knowledge ===")
            for i, c in enumerate(chunks, 1):
                parts.append(
                    f"--- Source {i}: {c.page} > {c.heading} "
                    f"[{c.game_mode}] (Score: {c.score:.2f}) ---\n"
                    f"{c.text}\n"
                )

        if table_results:
            parts.append("\n=== Structured Data ===")
            for i, t in enumerate(table_results, 1):
                # Flatten data dict to readable string
                data_str = ", ".join(
                    f"{k}: {v}" for k, v in t.data.items()
                    if v and str(v).strip() and k != "_id"
                )
                parts.append(
                    f"--- Table {i}: {t.page} [{t.game_mode}/{t.category}] ---\n"
                    f"{data_str}\n"
                )

        if not parts:
            return "[No relevant data found in Wiki.]"

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Quick Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Test RAG Pipeline V3.0 (Hybrid) ===\n")
    rag = RAGPipeline()

    test_queries = [
        ("Hunter class skills", "NGS"),
        ("What weapon does Braver use?", "NGS"),
        ("Rivalate series stats", "PSO2"),
    ]

    for q, gm in test_queries:
        print(f"Q: {q} (game={gm})")
        print("-" * 50)

        chunks = rag.search_chunks(q, top_k=2, game_mode=gm)
        for c in chunks:
            print(f"  [CHUNK {c.score:.3f}] {c.page} > {c.heading}")
            print(f"    {c.text[:100]}...")

        tables = rag.search_tables(q, game_mode=gm, limit=2)
        for t in tables:
            print(f"  [TABLE] {t.page} [{t.category}]")
            print(f"    {dict(list(t.data.items())[:4])}")

        print()
