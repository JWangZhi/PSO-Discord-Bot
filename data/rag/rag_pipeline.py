"""
RAG Pipeline - Retrieve Knowledge from Vector Database.

This pipeline is responsible for:
1. Receiving questions from User (or Agent).
2. Converting questions to Vectors using Local Embedding (LM Studio).
3. Searching for the most relevant document chunks in Pinecone.
4. Returning Content to LLMs (Gemini/Groq) to synthesize an answer.
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from pinecone import Pinecone

import config


@dataclass
class RetrievedChunk:
    """A chunk of data retrieved from Vector DB."""

    text: str
    source: str  # Original Wiki URL
    page: str  # Page name (e.g: Hunter)
    heading: str  # Heading name in page
    score: float  # Similarity score (0.0 - 1.0)


class RAGPipeline:
    """Retrieval-Augmented Generation System.

    - Embedding: Local (LM Studio - EmbeddingGemma-300m)
    - Vector DB: Pinecone Cloud
    - LLM: Gemini / Groq (Cloud API) - called outside this pipeline
    """

    def __init__(self):
        # Local Embedding client (LM Studio)
        self._embed_client = OpenAI(
            base_url=config.LOCAL_EMBED_URL,
            api_key="lm-studio",
        )

        # Pinecone client
        pc = Pinecone(api_key=config.PINECONE_API_KEY)
        self._index = pc.Index("pso2-wiki")

    def embed_query(self, query: str) -> list[float]:
        """Convert text query to vector using Local Embedding.

        Args:
            query: User's question.

        Returns:
            768-dimensional Vector.
        """
        response = self._embed_client.embeddings.create(
            model=config.LOCAL_EMBED_MODEL,
            input=[query],
        )
        return response.data[0].embedding

    def search(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        """Search for the most relevant chunks in Pinecone.

        Args:
            query: User's question.
            top_k: Number of results to return.

        Returns:
            List of RetrievedChunks sorted by relevance.
        """
        query_vector = self.embed_query(query)

        results = self._index.query(
            vector=query_vector,
            top_k=top_k,
            include_metadata=True,
        )

        chunks = []
        for match in results.matches:
            meta = match.metadata or {}
            chunks.append(RetrievedChunk(
                text=meta.get("text", ""),
                source=meta.get("source", ""),
                page=meta.get("page", ""),
                heading=meta.get("heading", ""),
                score=match.score,
            ))

        return chunks

    def retrieve_context(self, query: str, top_k: int = 5) -> str:
        """Main function - Get raw context text to insert into LLM Prompt.

        Args:
            query: User's question.
            top_k: Number of chunks to retrieve.

        Returns:
            Concatenated string of text chunks, ready for System Prompt.
        """
        chunks = self.search(query, top_k=top_k)

        if not chunks:
            return "[No relevant data found in Wiki.]"

        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            context_parts.append(
                f"--- Source {i}: {chunk.page} > {chunk.heading} "
                f"(Score: {chunk.score:.2f}) ---\n"
                f"{chunk.text}\n"
            )

        return "\n".join(context_parts)


# --- Quick Test ---
if __name__ == "__main__":
    print("=== Test RAG Pipeline ===\n")
    rag = RAGPipeline()

    test_queries = [
        "Hunter class skills and abilities",
        "What weapon does Braver use?",
        "How does Force cast techniques?",
    ]

    for q in test_queries:
        print(f"Q: {q}")
        print("-" * 50)
        chunks = rag.search(q, top_k=3)
        for c in chunks:
            print(f"  [{c.score:.3f}] {c.page} > {c.heading}")
            print(f"  {c.text[:120]}...")
        print()
