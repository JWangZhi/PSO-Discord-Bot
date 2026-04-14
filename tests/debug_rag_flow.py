"""
Quick diagnostic: show exactly what Pinecone returns for test queries.
Run: uv run tests/debug_rag_flow.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.rag.rag_pipeline import RAGPipeline

rag = RAGPipeline()

# 1. Check index stats
stats = rag._index.describe_index_stats()
print("=" * 60)
print(f"Pinecone Index: pso2-wiki")
print(f"  Total vectors:  {stats.total_vector_count}")
print(f"  Dimension:      {stats.dimension}")
if stats.namespaces:
    for ns, ns_stats in stats.namespaces.items():
        print(f"  Namespace '{ns}': {ns_stats.vector_count} vectors")
print("=" * 60)

# 2. Test queries
test_queries = [
    ("show me all skill of Ranger class", "NGS"),
    ("Ranger Blight Rounds potency", "NGS"),
    ("list Ranger skills", "NGS"),
    ("Slayer Gunblade Focus Overdrive", "NGS"),
    ("Phantom skills", "PSO2"),
]

for query, game_mode in test_queries:
    print(f"\n{'─' * 60}")
    print(f"QUERY: \"{query}\" | GAME: {game_mode}")
    print(f"{'─' * 60}")

    chunks = rag.search_chunks(query, top_k=5, game_mode=game_mode)
    if not chunks:
        print("  [NO RESULTS]")
        continue

    for i, c in enumerate(chunks, 1):
        page_info = f"{c.page} > {c.heading}"
        if c.sub_heading:
            page_info += f" > {c.sub_heading}"
        print(f"  [{i}] Score={c.score:.4f} | {page_info}")
        print(f"      Source: {c.source}")
        # Show first 150 chars of content
        preview = c.text[:150].replace('\n', ' ')
        print(f"      Text: {preview}...")
        print()

    # Show full context that would be sent to LLM
    print(f"\n  FULL CONTEXT (first 500 chars):")
    ctx = rag.retrieve_context(query, game_mode=game_mode, top_k=5)
    print(f"  {ctx[:500]}")
    print()
