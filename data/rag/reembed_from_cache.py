"""
Re-extract chunks from cached HTML → embed via LM Studio → upload to Pinecone.

Uses the same extract_text_chunks() logic from wiki_scraper.py (Phase 2.5).

Usage:
  python data/rag/reembed_from_cache.py              # all cached pages
  python data/rag/reembed_from_cache.py --dry-run     # extract only, no upload
  python data/rag/reembed_from_cache.py --clear        # clear index before upload
"""

import sys
import json
import time
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from pinecone import Pinecone

from settings import env as config
from settings import app as app_settings
from settings.scraper import CACHE_DIR, MANIFEST_FILE, STORAGE_DIR
from data.scrapers.wiki_scraper import (
    extract_text_chunks,
    _safe_filename,
    _ensure_dir,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EMBED_BATCH_SIZE = 32  # LM Studio handles batches fine
PINECONE_UPSERT_BATCH = 100
DRY_RUN = "--dry-run" in sys.argv
CLEAR_INDEX = "--clear" in sys.argv


# ---------------------------------------------------------------------------
# Build page_path → (game_mode, category, scrape_type) from manifest
# ---------------------------------------------------------------------------
def build_manifest_map() -> dict[str, dict]:
    """Returns { page_path: { game_mode, category, scrape_type } }."""
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    mapping = {}
    for game_mode, sections in manifest.items():
        for _section_name, section_data in sections.items():
            category = section_data.get("category", "general")
            scrape_type = section_data.get("scrape_type", "mixed")
            for page_path in section_data.get("pages", []):
                mapping[page_path] = {
                    "game_mode": game_mode,
                    "category": category,
                    "scrape_type": scrape_type,
                }
    return mapping


def cache_filename_to_page_path(filename: str, manifest_map: dict) -> str | None:
    """Reverse-map a cache filename back to its manifest page_path.

    Cache files: Portal_New_Genesis_Slayer.html → Portal:New_Genesis/Slayer
    PSO2 files:  Hunter.html → Hunter
    """
    stem = Path(filename).stem  # e.g. Portal_New_Genesis_Slayer

    # Try to match against all known page paths
    for page_path in manifest_map:
        if _safe_filename(page_path) == stem:
            return page_path

    return None


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
    print("  Re-extract + Re-embed from Cache")
    print(f"  Cache dir  : {CACHE_DIR}")
    print(f"  Dry run    : {DRY_RUN}")
    print(f"  Clear index: {CLEAR_INDEX}")
    print("=" * 60)

    # 1. Build manifest map
    manifest_map = build_manifest_map()
    print(f"\n[INFO] Manifest has {len(manifest_map)} page paths")

    # 2. Scan cache HTML files
    html_files = sorted(CACHE_DIR.glob("*.html"))
    print(f"[INFO] Found {len(html_files)} cached HTML files")

    # 3. Re-extract chunks from all cached HTML
    all_chunks = []
    matched = 0
    skipped = 0

    for html_file in html_files:
        page_path = cache_filename_to_page_path(html_file.name, manifest_map)
        if not page_path:
            skipped += 1
            continue

        meta = manifest_map[page_path]
        game_mode = meta["game_mode"]
        category = meta["category"]
        scrape_type = meta["scrape_type"]

        # Only extract text chunks (for RAG embedding)
        if scrape_type == "structured_table":
            # structured_table pages have no prose, skip text extraction
            # but we still want skill tables if any
            pass

        html = html_file.read_text(encoding="utf-8")
        page_title = page_path.split("/")[-1].replace("_", " ")

        chunks = extract_text_chunks(html, game_mode, category, page_path, page_title)
        if chunks:
            all_chunks.extend(chunks)
            matched += 1

        # Also save updated chunks.json locally
        out_dir = STORAGE_DIR / game_mode.upper() / category
        _ensure_dir(out_dir)
        chunks_file = out_dir / (_safe_filename(page_path) + ".chunks.json")
        with open(chunks_file, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)

    print(f"\n[INFO] Extracted {len(all_chunks)} chunks from {matched} pages ({skipped} unmatched cache files)")

    if not all_chunks:
        print("[WARN] No chunks to embed. Exiting.")
        return

    if DRY_RUN:
        print("\n[DRY RUN] Skipping embedding + upload. Sample chunks:")
        for c in all_chunks[:3]:
            print(f"  [{c['game_mode']}] {c['page_title']} > {c['section']} > {c['sub_section']} ({len(c['content'])} chars)")
        print(f"  ... and {len(all_chunks) - 3} more")
        return

    # 4. Connect to embedding model + Pinecone
    embed_client = OpenAI(base_url=config.LOCAL_EMBED_URL, api_key="lm-studio")
    pc = Pinecone(api_key=config.PINECONE_API_KEY)
    index = pc.Index(app_settings.RAG_WIKI_INDEX_NAME)

    # 5. Clear index if requested
    if CLEAR_INDEX:
        print("\n[WARN] Clearing all vectors from Pinecone index...")
        index.delete(delete_all=True)
        time.sleep(2)
        print("[OK] Index cleared.")

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
