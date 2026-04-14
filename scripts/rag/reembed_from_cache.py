"""
Load pre-extracted chunks from wiki_raw/**/*.chunks.json → embed via LM Studio → upload to Pinecone.

Source: data/storage/wiki_raw/**/*.chunks.json  (written by wiki_scraper.py)
NOT from HTML cache/ — those are raw and may be stale.

Usage:
  python data/rag/reembed_from_cache.py              # embed all chunks.json
  python data/rag/reembed_from_cache.py --dry-run     # count only, no upload
  python data/rag/reembed_from_cache.py --clear        # clear index before upload
"""

import sys
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from pinecone import Pinecone

from settings import env as config
from settings import app as app_settings
from settings.scraper import STORAGE_DIR

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WIKI_RAW_DIR = STORAGE_DIR  # data/storage/wiki_raw/
EMBED_BATCH_SIZE = 32
PINECONE_UPSERT_BATCH = 100
DRY_RUN = "--dry-run" in sys.argv
CLEAR_INDEX = "--clear" in sys.argv


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
def embed_texts(client: OpenAI, texts: list[str], model: str) -> list[list[float]]:
    """Embed a batch of texts via LM Studio."""
    response = client.embeddings.create(model=model, input=texts)
    return [item.embedding for item in response.data]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  Re-embed from wiki_raw chunks.json")
    print(f"  Source dir : {WIKI_RAW_DIR}")
    print(f"  Dry run    : {DRY_RUN}")
    print(f"  Clear index: {CLEAR_INDEX}")
    print("=" * 60)

    # 1. Scan all .chunks.json files under wiki_raw/
    chunk_files = sorted(WIKI_RAW_DIR.rglob("*.chunks.json"))
    print(f"\n[INFO] Found {len(chunk_files)} .chunks.json files")

    if not chunk_files:
        print("[ERROR] No .chunks.json files found. Run wiki_scraper.py first.")
        return

    # 2. Load all chunks
    all_chunks = []
    loaded_files = 0
    for chunk_file in chunk_files:
        try:
            data = json.loads(chunk_file.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                all_chunks.extend(data)
                loaded_files += 1
        except Exception as e:
            print(f"  [WARN] Could not read {chunk_file.name}: {e}")

    print(f"[INFO] Loaded {len(all_chunks)} chunks from {loaded_files} files")

    if not all_chunks:
        print("[WARN] No chunks to embed. Exiting.")
        return

    if DRY_RUN:
        print("\n[DRY RUN] Skipping embedding + upload. Sample chunks:")
        for c in all_chunks[:3]:
            print(f"  [{c.get('game_mode','-')}] {c.get('page_title','-')} > {c.get('section','-')} > {c.get('sub_section','-')} ({len(c.get('content',''))} chars)")
        print(f"  ... and {len(all_chunks) - 3} more")
        return

    # 4. Connect to embedding model + Pinecone
    embed_client = OpenAI(base_url=config.LOCAL_EMBED_URL, api_key="lm-studio")
    pc = Pinecone(api_key=config.PINECONE_API_KEY)
    index = pc.Index(app_settings.RAG_WIKI_INDEX_NAME)

    # 5. Clear index if requested
    if CLEAR_INDEX:
        print("\n[WARN] Clearing all vectors from Pinecone index...")
        try:
            index.delete(delete_all=True, namespace="")
            time.sleep(2)
            print("[OK] Index cleared.")
        except Exception as e:
            if "Namespace not found" in str(e) or "404" in str(e):
                print("[OK] Index already empty, nothing to clear.")
            else:
                raise

    # 6. Embed in batches
    print(f"\n[INFO] Embedding {len(all_chunks)} chunks (batch_size={EMBED_BATCH_SIZE})...")
    vectors = []
    for i in range(0, len(all_chunks), EMBED_BATCH_SIZE):
        batch = all_chunks[i:i + EMBED_BATCH_SIZE]
        texts = [c["content"] for c in batch]

        try:
            embeddings = embed_texts(embed_client, texts, config.LOCAL_EMBED_MODEL)
        except Exception as e:
            print(f"  [ERROR] Embedding batch {i // EMBED_BATCH_SIZE}: {e}")
            continue

        for chunk, emb in zip(batch, embeddings):
            # Pinecone metadata limit: 40 960 bytes per vector.
            # Truncate text to ~35 000 bytes to leave room for other fields.
            text = chunk["content"]
            text_bytes = text.encode("utf-8")
            if len(text_bytes) > 35_000:
                text = text_bytes[:35_000].decode("utf-8", errors="ignore")

            vectors.append({
                "id": chunk["chunk_id"],
                "values": emb,
                "metadata": {
                    "text": text,
                    "game_mode": chunk["game_mode"],
                    "category": chunk["category"],
                    "page_title": chunk["page_title"],
                    "section": chunk["section"],
                    "sub_section": chunk["sub_section"],
                    "url": chunk["url"],
                },
            })

        done = min(i + EMBED_BATCH_SIZE, len(all_chunks))
        print(f"  Embedded {done}/{len(all_chunks)}")

    # 7. Upsert to Pinecone in batches
    print(f"\n[INFO] Uploading {len(vectors)} vectors to Pinecone (batch_size={PINECONE_UPSERT_BATCH})...")
    for i in range(0, len(vectors), PINECONE_UPSERT_BATCH):
        batch = vectors[i:i + PINECONE_UPSERT_BATCH]
        try:
            index.upsert(vectors=batch)
        except Exception as e:
            print(f"  [ERROR] Upsert batch {i // PINECONE_UPSERT_BATCH}: {e}")
            continue
        done = min(i + PINECONE_UPSERT_BATCH, len(vectors))
        print(f"  Upserted {done}/{len(vectors)}")

    # 8. Verify
    time.sleep(3)
    stats = index.describe_index_stats()
    print(f"\n[OK] Pinecone index stats: {stats.total_vector_count} vectors")
    print("Done!")


if __name__ == "__main__":
    main()
