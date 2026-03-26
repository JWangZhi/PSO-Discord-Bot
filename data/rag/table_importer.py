"""
Table Importer — Read .tables.json → Upsert to MongoDB Atlas.

Usage:
  uv run python data/rag/table_importer.py             # Upsert (idempotent)
  uv run python data/rag/table_importer.py --purge      # Delete all, then import
"""

import sys
import json
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pymongo import MongoClient, UpdateOne
import config

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WIKI_RAW_DIR   = Path(__file__).parent.parent / "storage" / "wiki_raw"
DB_NAME        = "pso2_bot"
COLLECTION     = "wiki_tables"
UPSERT_BATCH   = 100

PURGE_MODE = "--purge" in sys.argv


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------

def get_collection():
    client = MongoClient(config.MONGODB_URI)
    db = client[DB_NAME]
    coll = db[COLLECTION]

    # Create indexes for fast query
    coll.create_index("game_mode")
    coll.create_index("category")
    coll.create_index([("game_mode", 1), ("category", 1)])

    return coll


def purge_collection(coll):
    """Delete all documents after user confirmation."""
    count = coll.count_documents({})

    if count == 0:
        print("[Purge] Collection is already empty.")
        return

    confirm = input(
        f"This will DELETE ALL {count} documents in '{COLLECTION}'. "
        f"Type 'yes' to confirm: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    result = coll.delete_many({})
    print(f"[Purge] Deleted {result.deleted_count} documents.")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_all_tables() -> list[dict]:
    """Recursively load .tables.json and flatten rows into documents."""
    all_docs = []
    table_files = sorted(WIKI_RAW_DIR.rglob("*.tables.json"))

    if not table_files:
        print("[Loader] No .tables.json files found. Run wiki_scraper.py first.")
        sys.exit(1)

    for f in table_files:
        with open(f, "r", encoding="utf-8") as fh:
            tables = json.load(fh)

        for table in tables:
            for row in table.get("rows", []):
                # Each row already has _game_mode, _category, _page, _source_url
                doc = {
                    "game_mode":  row.pop("_game_mode", "").upper(),
                    "category":   row.pop("_category", ""),
                    "page":       row.pop("_page", ""),
                    "source_url": row.pop("_source_url", ""),
                    "table_index": table.get("table_index", 0),
                    "data":       row,  # All remaining columns as nested dict
                }
                all_docs.append(doc)

    print(f"[Loader] Found {len(table_files)} files, {len(all_docs)} total rows.")
    return all_docs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def import_tables():
    """Main flow: Load table rows → Upsert to MongoDB."""
    coll = get_collection()

    if PURGE_MODE:
        purge_collection(coll)

    docs = load_all_tables()

    if not docs:
        print("[Import] No data to import.")
        return

    # Batch upsert using insert_many (since we purged, or just add new)
    total = 0
    for i in range(0, len(docs), UPSERT_BATCH):
        batch = docs[i:i + UPSERT_BATCH]
        coll.insert_many(batch)
        total += len(batch)
        print(f"  [{total}/{len(docs)}] inserted")

    print(f"\n[Done] Imported {total} rows into MongoDB '{DB_NAME}.{COLLECTION}'.")
    print(f"  Total docs in collection: {coll.count_documents({})}")


if __name__ == "__main__":
    import_tables()
