"""
Embedding Uploader - Converts Wiki data to Vectors and uploads to Pinecone.

Processing flow:
1. Read scraped Markdown files from data/storage/wiki_raw/
2. Chunk into smaller segments ~500 words.
3. Call Local Embedding API to convert text -> 768d vector.
4. Push vectors to Pinecone Index.
"""

import sys
import time
import hashlib
from pathlib import Path

# Ensure project root is in sys.path to import config
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from google import genai
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec # pylint: disable=no-name-in-module

import config

# Directory containing scraped Wiki data
WIKI_RAW_DIR = Path(__file__).parent.parent / "storage" / "wiki_raw"

# Pinecone Configuration
PINECONE_INDEX_NAME = "pso2-wiki"
EMBEDDING_DIMENSION = 768  # EmbeddingGemma-300m output


def chunk_text(text: str, max_words: int = 400) -> list[dict]:
    """Cut Markdown text into smaller chunks by heading.

    Prioritize splitting by heading (##, ###). If a section is too long,
    automatically split further by word count.

    Returns:
        List of {"text": str, "heading": str}
    """
    chunks = []
    current_heading = "Introduction"
    current_lines = []

    for line in text.split("\n"):
        # Detect new heading
        if line.startswith("## ") or line.startswith("### "):
            # Save current chunk
            if current_lines:
                chunk_text_str = "\n".join(current_lines).strip()
                if len(chunk_text_str.split()) > 10:  # Skip too short chunks
                    chunks.append({
                        "text": chunk_text_str,
                        "heading": current_heading,
                    })
            current_heading = line.lstrip("#").strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    # Add final chunk
    if current_lines:
        chunk_text_str = "\n".join(current_lines).strip()
        if len(chunk_text_str.split()) > 10:
            chunks.append({
                "text": chunk_text_str,
                "heading": current_heading,
            })

    # Split further if chunk is too long
    final_chunks = []
    for chunk in chunks:
        words = chunk["text"].split()
        if len(words) > max_words:
            for i in range(0, len(words), max_words):
                sub_text = " ".join(words[i: i + max_words])
                final_chunks.append({
                    "text": sub_text,
                    "heading": chunk["heading"],
                })
        else:
            final_chunks.append(chunk)

    return final_chunks


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Call Local Embedding API (LM Studio) to convert text -> vector.

    Uses OpenAI-compatible endpoint from LM Studio running EmbeddingGemma-300m.

    Args:
        texts: List of sentences/paragraphs to embed.

    Returns:
        List of 768-dimensional vectors.
    """
    client = OpenAI(
        base_url=config.LOCAL_EMBED_URL,
        api_key="lm-studio",  # LM Studio does not require a real key
    )

    response = client.embeddings.create(
        model=config.LOCAL_EMBED_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


def init_pinecone_index() -> object:
    """Initialize or connect to Pinecone Index.

    If the index exists but has the wrong dimension, it will be deleted and recreated.

    Returns:
        Pinecone Index object.
    """
    pc = Pinecone(api_key=config.PINECONE_API_KEY)

    existing_indexes = {idx.name: idx for idx in pc.list_indexes()}

    if PINECONE_INDEX_NAME in existing_indexes:
        idx_info = existing_indexes[PINECONE_INDEX_NAME]
        if idx_info.dimension != EMBEDDING_DIMENSION:
            print(f"[Pinecone] Old index has dimension {idx_info.dimension}, need {EMBEDDING_DIMENSION}. Deleting...")
            pc.delete_index(PINECONE_INDEX_NAME)
            time.sleep(3)
        else:
            print(f"[Pinecone] Connected to index: {PINECONE_INDEX_NAME}")
            return pc.Index(PINECONE_INDEX_NAME)

    print(f"[Pinecone] Creating new index: {PINECONE_INDEX_NAME} (dim={EMBEDDING_DIMENSION})")
    pc.create_index(
        name=PINECONE_INDEX_NAME,
        dimension=EMBEDDING_DIMENSION,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    time.sleep(10)  # Wait for index to be ready

    return pc.Index(PINECONE_INDEX_NAME)


def make_id(page_name: str, chunk_idx: int) -> str:
    """Create a unique ID for each vector."""
    raw = f"{page_name}_{chunk_idx}"
    return hashlib.md5(raw.encode()).hexdigest()


def upload_wiki_to_pinecone():
    """Main function: Read all Wiki files -> Chunk -> Embed -> Upload."""
    md_files = list(WIKI_RAW_DIR.glob("*.md"))
    if not md_files:
        print("[Upload] No files found in wiki_raw/. Please run the scraper first.")
        return

    print(f"[Upload] Found {len(md_files)} Wiki files.")

    index = init_pinecone_index()
    total_vectors = 0

    for md_file in md_files:
        page_name = md_file.stem  # vd: "Hunter"
        print(f"\n[Upload] Processing: {page_name}")

        content = md_file.read_text(encoding="utf-8")
        chunks = chunk_text(content)
        print(f"  -> {len(chunks)} chunks")

        if not chunks:
            continue

        # Embed in batches (max 100/API call)
        texts = [c["text"] for c in chunks]
        batch_size = 50
        all_vectors = []

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i: i + batch_size]
            batch_chunks = chunks[i: i + batch_size]

            print(f"  -> Embedding batch {i // batch_size + 1}...")
            embeddings = embed_texts(batch_texts)

            for j, (emb, chunk) in enumerate(zip(embeddings, batch_chunks)):
                vec_id = make_id(page_name, i + j)
                all_vectors.append({
                    "id": vec_id,
                    "values": emb,
                    "metadata": {
                        "page": page_name,
                        "heading": chunk["heading"],
                        "text": chunk["text"][:1000],  # Pinecone metadata limit
                        "source": f"https://pso2na.arks-visiphone.com/wiki/{page_name}",
                    },
                })

            time.sleep(0.5)  # Rate limit

        # Upload to Pinecone in batches
        upsert_batch = 50
        for i in range(0, len(all_vectors), upsert_batch):
            batch = all_vectors[i: i + upsert_batch]
            index.upsert(vectors=[(v["id"], v["values"], v["metadata"]) for v in batch])

        total_vectors += len(all_vectors)
        print(f"  -> Uploaded {len(all_vectors)} vectors.")

    print(f"\n[Upload] Completed! A total of {total_vectors} vectors pushed to Pinecone.")


if __name__ == "__main__":
    upload_wiki_to_pinecone()
