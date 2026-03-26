"""
Embedding Uploader V3.0 — Read .chunks.json → Embed → Upload to Pinecone.

Usage:
  uv run python data/rag/embed_uploader.py             # Upsert (idempotent)
  uv run python data/rag/embed_uploader.py --purge      # Delete all, then upload
"""

import sys
import time
import json
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec  # pylint: disable=no-name-in-module

import config

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WIKI_RAW_DIR       = Path(__file__).parent.parent / "storage" / "wiki_raw"
PINECONE_INDEX     = "pso2-wiki"
EMBEDDING_DIM      = 768
EMBED_BATCH_SIZE   = 50
UPSERT_BATCH_SIZE  = 50
PINECONE_META_LIMIT = 500  # bytes, conservative under 512

PURGE_MODE = "--purge" in sys.argv


# ---------------------------------------------------------------------------
# Embedding Client
# ---------------------------------------------------------------------------

def get_embed_client() -> OpenAI:
    return OpenAI(
        base_url=config.LOCAL_EMBED_URL,
        api_key="lm-studio",
    )


def embed_texts(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Batch embed texts via Local Embedding API."""
    response = client.embeddings.create(
        model=config.LOCAL_EMBED_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


# ---------------------------------------------------------------------------
# Pinecone
# ---------------------------------------------------------------------------

def init_pinecone() -> object:
    """Connect to or create the Pinecone index."""
    pc = Pinecone(api_key=config.PINECONE_API_KEY)
    existing = {idx.name for idx in pc.list_indexes()}

    if PINECONE_INDEX not in existing:
        print(f"[Pinecone] Creating index: {PINECONE_INDEX} (dim={EMBEDDING_DIM})")
        pc.create_index(
            name=PINECONE_INDEX,
            dimension=EMBEDDING_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        time.sleep(10)
    else:
        print(f"[Pinecone] Connected to: {PINECONE_INDEX}")

    return pc.Index(PINECONE_INDEX)


def purge_index(index):
    """Delete all vectors after user confirmation."""
    stats = index.describe_index_stats()
    count = stats.total_vector_count

    if count == 0:
        print("[Purge] Index is already empty.")
        return

    confirm = input(
        f"This will DELETE ALL {count} vectors in '{PINECONE_INDEX}'. "
        f"Type 'yes' to confirm: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    index.delete(delete_all=True)
    print(f"[Purge] Deleted {count} vectors.")
    time.sleep(2)


# ---------------------------------------------------------------------------
# Chunk Loader
# ---------------------------------------------------------------------------

def load_all_chunks() -> list[dict]:
    """Recursively load all .chunks.json from wiki_raw."""
    all_chunks = []
    chunk_files = sorted(WIKI_RAW_DIR.rglob("*.chunks.json"))

    if not chunk_files:
        print("[Loader] No .chunks.json files found. Run wiki_scraper.py first.")
        sys.exit(1)

    for f in chunk_files:
        with open(f, "r", encoding="utf-8") as fh:
            chunks = json.load(fh)
            all_chunks.extend(chunks)

    print(f"[Loader] Found {len(chunk_files)} files, {len(all_chunks)} total chunks.")
    return all_chunks


def truncate_metadata_value(value: str, max_bytes: int = PINECONE_META_LIMIT) -> str:
    """Truncate string to fit within Pinecone metadata byte limit."""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# Main Upload
# ---------------------------------------------------------------------------

def upload():
    """Main flow: Load chunks → Embed → Upsert to Pinecone."""
    embed_client = get_embed_client()
    index = init_pinecone()

    if PURGE_MODE:
        purge_index(index)

    chunks = load_all_chunks()

    total_uploaded = 0

    for i in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[i:i + EMBED_BATCH_SIZE]
        texts = [c["content"] for c in batch]

        # Embed
        try:
            embeddings = embed_texts(embed_client, texts)
        except Exception as e:
            print(f"  [ERROR] Embedding failed at batch {i // EMBED_BATCH_SIZE + 1}: {e}")
            continue

        # Build vectors
        vectors = []
        for chunk, emb in zip(batch, embeddings):
            vec_id = chunk["chunk_id"]
            metadata = {
                "game_mode":   chunk.get("game_mode", ""),
                "category":    chunk.get("category", ""),
                "page_title":  chunk.get("page_title", ""),
                "section":     chunk.get("section", ""),
                "sub_section": chunk.get("sub_section", ""),
                "url":         chunk.get("url", ""),
                "text":        truncate_metadata_value(chunk["content"]),
            }
            vectors.append((vec_id, emb, metadata))

        # Upsert to Pinecone
        for j in range(0, len(vectors), UPSERT_BATCH_SIZE):
            upsert_batch = vectors[j:j + UPSERT_BATCH_SIZE]
            index.upsert(vectors=upsert_batch)

        total_uploaded += len(vectors)
        progress = min(i + EMBED_BATCH_SIZE, len(chunks))
        print(f"  [{progress}/{len(chunks)}] embedded + upserted")

        time.sleep(0.3)

    # Final stats
    time.sleep(2)
    stats = index.describe_index_stats()
    print(f"\n[Done] Uploaded {total_uploaded} vectors. Index total: {stats.total_vector_count}")


if __name__ == "__main__":
    upload()
