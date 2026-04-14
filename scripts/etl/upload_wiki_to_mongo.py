"""
Upload wiki_raw data to MongoDB — replaces Pinecone/vector pipeline entirely.

Reads pre-extracted .chunks.json, .tables.json, .meta.json from
data/storage/wiki_raw/ and upserts into 3 MongoDB collections:

  wiki_chunks  — text chunks (prose, skill descriptions)
  wiki_tables  — structured table rows with _search_text for text search
  wiki_pages   — per-page metadata (what exists, last scraped)

Usage:
  uv run data/etl/upload_wiki_to_mongo.py                # upload all
  uv run data/etl/upload_wiki_to_mongo.py --dry-run       # preview only
  uv run data/etl/upload_wiki_to_mongo.py --clear          # drop collections first
  uv run data/etl/upload_wiki_to_mongo.py --stats          # print stats and exit
"""

# ── Standard Library ──────────────────────────────────────────────────────────
import argparse
import ast
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, TypedDict

# ── Path bootstrap (must precede local imports) ───────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Third-party ───────────────────────────────────────────────────────────────
import pymongo.errors
from pymongo import ReplaceOne

# ── Local / project ───────────────────────────────────────────────────────────
from core.db import MongoDB
from settings.scraper import STORAGE_DIR  # data/storage/wiki_raw

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
WIKI_RAW_DIR = STORAGE_DIR
_BATCH_SIZE = 500


class CollectionNames:
    CHUNKS = "wiki_chunks"
    TABLES = "wiki_tables"
    PAGES = "wiki_pages"


class WikiFields:
    ID = "_id"
    GAME_MODE = "game_mode"
    CATEGORY = "category"
    PAGE_NAME = "page_name"
    PAGE_TITLE = "page_title"
    SECTION = "section"
    SUB_SECTION = "sub_section"
    CONTENT = "content"
    URL = "url"
    LAST_SCRAPED = "last_scraped"
    CHUNK_ID = "chunk_id"
    TABLE_INDEX = "table_index"
    HEADERS = "headers"
    ROWS = "rows"
    ROW_COUNT = "row_count"
    SEARCH_TEXT = "_search_text"
    CHUNK_COUNT = "chunk_count"
    TABLE_COUNT = "table_count"


# ── Typed data structures ─────────────────────────────────────────────────────
class PageInfo(TypedDict):
    game_mode: str
    category: str
    page_name: str


class ChunkDocument(TypedDict):
    _id: str
    game_mode: str
    category: str
    page_title: str
    section: str
    sub_section: str
    content: str
    url: str
    last_scraped: str


class TableDocument(TypedDict):
    _id: str
    game_mode: str
    category: str
    page_name: str
    table_index: int
    headers: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    _search_text: str


class PageDocument(TypedDict):
    _id: str
    game_mode: str
    category: str
    page_name: str
    page_title: str
    url: str
    last_scraped: str
    chunk_count: int
    table_count: int


class GameModeStats(TypedDict):
    pages: int
    chunks: int
    tables: int


# ── Transformation helpers ────────────────────────────────────────────────────
def parse_wiki_path(filepath: Path) -> PageInfo | None:
    """Extract game_mode, category, page_name from a wiki_raw file path.

    Example: wiki_raw/NGS/class/Portal_New_Genesis_Hunter.chunks.json
    → { game_mode: "NGS", category: "class", page_name: "Portal_New_Genesis_Hunter" }
    Returns None if the path cannot be resolved or has fewer than 3 components.
    """
    try:
        rel = filepath.relative_to(WIKI_RAW_DIR)
    except ValueError:
        return None
    parts = rel.parts  # ('NGS', 'class', 'Portal_New_Genesis_Hunter.chunks.json')
    if len(parts) < 3:
        return None
    return PageInfo(
        game_mode=parts[0],
        category=parts[1],
        page_name=parts[2].split(".")[0],
    )


def is_nav_table(headers: list[str]) -> bool:
    """Detect navigation/junk tables (all-numeric or underscore-prefixed headers)."""
    clean = [h for h in headers if not h.startswith("_")]
    if not clean:
        return True
    return all(h.isdigit() for h in clean)


def flatten_multi_level_header(header: str) -> str:
    """Flatten tuple-style multi-level headers.

    "('treasure shop ()', 'name')" → "treasure_shop:name"
    "('power', 'lv.1')"           → "power:lv.1"
    Normal headers pass through unchanged.
    """
    if not header.startswith("("):
        return header

    try:
        parts = ast.literal_eval(header)
        if isinstance(parts, tuple) and len(parts) >= 2:
            group = re.sub(r"\s*\([^)]*\)\s*", "", str(parts[0])).strip()
            group = re.sub(r"\s+", "_", group).lower()
            sub = str(parts[1]).strip()
            # Skip duplicate suffixes like ".1", ".2" that pandas adds
            if re.match(r"^" + re.escape(group) + r"(\.\d+)?$", sub.lower()):
                return group
            return f"{group}:{sub}"
    except (ValueError, SyntaxError):
        pass
    return header


def build_search_text(headers: list[str], rows: list[dict[str, Any]]) -> str:
    """Join all cell values into one searchable string."""
    parts: list[str] = []
    for row in rows:
        for key, val in row.items():
            if key.startswith("_"):
                continue
            s = str(val).strip()
            if s and s.lower() != "nan":
                parts.append(s)
    return " ".join(parts)


def process_table(
    table: dict[str, Any], page_info: PageInfo, table_index: int
) -> TableDocument | None:
    """Process a single raw table into a MongoDB document.

    Returns None if the table is junk (nav/empty).
    """
    headers: list[str] = table.get(WikiFields.HEADERS, [])
    rows: list[dict[str, Any]] = table.get(WikiFields.ROWS, [])
    row_count: int = table.get(WikiFields.ROW_COUNT, len(rows))

    if is_nav_table(headers):
        return None
    if row_count == 0:
        return None

    # Flatten multi-level headers
    clean_headers: list[str] = [
        flatten_multi_level_header(h) for h in headers if not h.startswith("_")
    ]

    # Clean rows: remove _-prefixed keys and nan values
    clean_rows: list[dict[str, Any]] = []
    for row in rows:
        clean: dict[str, Any] = {}
        for key, val in row.items():
            if key.startswith("_"):
                continue
            flat_key = flatten_multi_level_header(key)
            s = str(val).strip()
            if s.lower() == "nan" or s == "":
                continue
            clean[flat_key] = val
        if clean:
            clean_rows.append(clean)

    if not clean_rows:
        return None

    search_text = build_search_text(headers, rows)

    doc_id = (
        f"{page_info[WikiFields.GAME_MODE]}:{page_info[WikiFields.CATEGORY]}:"
        f"{page_info[WikiFields.PAGE_NAME]}:t{table_index}"
    )

    return {
        WikiFields.ID: doc_id,
        WikiFields.GAME_MODE: page_info[WikiFields.GAME_MODE],
        WikiFields.CATEGORY: page_info[WikiFields.CATEGORY],
        WikiFields.PAGE_NAME: page_info[WikiFields.PAGE_NAME],
        WikiFields.TABLE_INDEX: table_index,
        WikiFields.HEADERS: clean_headers,
        WikiFields.ROWS: clean_rows,
        WikiFields.ROW_COUNT: len(clean_rows),
        WikiFields.SEARCH_TEXT: search_text,
    }


def process_chunk(
    chunk: dict[str, Any], page_info: PageInfo, chunk_index: int
) -> ChunkDocument:
    """Process a single chunk into a MongoDB document."""
    chunk_id: str = chunk.get(
        WikiFields.CHUNK_ID,
        f"{page_info[WikiFields.GAME_MODE]}:{page_info[WikiFields.CATEGORY]}:"
        f"{page_info[WikiFields.PAGE_NAME]}:c{chunk_index}",
    )
    return {
        WikiFields.ID: chunk_id,
        WikiFields.GAME_MODE: chunk.get(WikiFields.GAME_MODE, page_info[WikiFields.GAME_MODE]),
        WikiFields.CATEGORY: chunk.get(WikiFields.CATEGORY, page_info[WikiFields.CATEGORY]),
        WikiFields.PAGE_TITLE: chunk.get(
            WikiFields.PAGE_TITLE,
            page_info[WikiFields.PAGE_NAME].replace("_", " "),
        ),
        WikiFields.SECTION: chunk.get(WikiFields.SECTION, ""),
        WikiFields.SUB_SECTION: chunk.get(WikiFields.SUB_SECTION, ""),
        WikiFields.CONTENT: chunk.get(WikiFields.CONTENT, ""),
        WikiFields.URL: chunk.get(WikiFields.URL, ""),
        WikiFields.LAST_SCRAPED: chunk.get(WikiFields.LAST_SCRAPED, ""),
    }


def process_meta(
    meta: dict[str, Any], page_info: PageInfo, chunk_count: int, table_count: int
) -> PageDocument:
    """Build a wiki_pages metadata document."""
    doc_id = (
        f"{page_info[WikiFields.GAME_MODE]}:{page_info[WikiFields.CATEGORY]}"
        f":{page_info[WikiFields.PAGE_NAME]}"
    )
    return {
        WikiFields.ID: doc_id,
        WikiFields.GAME_MODE: page_info[WikiFields.GAME_MODE],
        WikiFields.CATEGORY: page_info[WikiFields.CATEGORY],
        WikiFields.PAGE_NAME: page_info[WikiFields.PAGE_NAME],
        WikiFields.PAGE_TITLE: meta.get(
            WikiFields.PAGE_TITLE,
            page_info[WikiFields.PAGE_NAME].replace("_", " "),
        ),
        WikiFields.URL: meta.get(WikiFields.URL, ""),
        WikiFields.LAST_SCRAPED: meta.get(WikiFields.LAST_SCRAPED, ""),
        WikiFields.CHUNK_COUNT: chunk_count,
        WikiFields.TABLE_COUNT: table_count,
    }


def load_all_documents() -> tuple[
    list[ChunkDocument], list[TableDocument], list[PageDocument]
]:
    """Scan wiki_raw/ and return (chunks, tables, pages) document lists."""
    all_chunks: list[ChunkDocument] = []
    all_tables: list[TableDocument] = []
    all_pages: list[PageDocument] = []

    # Discover all pages by .meta.json files
    meta_files = sorted(WIKI_RAW_DIR.rglob("*.meta.json"))
    if not meta_files:
        logger.error("No .meta.json files found in %s", WIKI_RAW_DIR)
        return [], [], []

    for meta_file in meta_files:
        page_info = parse_wiki_path(meta_file)
        if not page_info:
            continue

        base = meta_file.with_suffix("").with_suffix("")  # strip .meta.json
        chunks_file = Path(str(base) + ".chunks.json")
        tables_file = Path(str(base) + ".tables.json")

        # Load meta
        meta: dict[str, Any] = {}
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in %s: %s", meta_file.name, e)
        except OSError as e:
            logger.warning("Cannot read %s: %s", meta_file.name, e)

        # Load chunks
        chunk_count = 0
        if chunks_file.exists():
            try:
                chunks = json.loads(chunks_file.read_text(encoding="utf-8"))
                if isinstance(chunks, list):
                    for i, c in enumerate(chunks):
                        doc = process_chunk(c, page_info, i)
                        all_chunks.append(doc)
                    chunk_count = len(chunks)
            except json.JSONDecodeError as e:
                logger.warning("Invalid JSON in %s: %s", chunks_file.name, e)
            except OSError as e:
                logger.warning("Cannot read %s: %s", chunks_file.name, e)

        # Load tables
        table_count = 0
        if tables_file.exists():
            try:
                tables = json.loads(tables_file.read_text(encoding="utf-8"))
                if isinstance(tables, list):
                    for i, t in enumerate(tables):
                        doc = process_table(t, page_info, i)
                        if doc:
                            all_tables.append(doc)
                            table_count += 1
            except json.JSONDecodeError as e:
                logger.warning("Invalid JSON in %s: %s", tables_file.name, e)
            except OSError as e:
                logger.warning("Cannot read %s: %s", tables_file.name, e)

        # Build page metadata
        page_doc = process_meta(meta, page_info, chunk_count, table_count)
        all_pages.append(page_doc)

    return all_chunks, all_tables, all_pages


def log_stats(
    chunks: list[ChunkDocument],
    tables: list[TableDocument],
    pages: list[PageDocument],
) -> None:
    """Log summary statistics."""
    sep = "=" * 60
    logger.info(sep)
    logger.info("  wiki_chunks : %6d documents", len(chunks))
    logger.info("  wiki_tables : %6d documents", len(tables))
    logger.info("  wiki_pages  : %6d documents", len(pages))
    logger.info(sep)

    if pages:
        by_game: dict[str, GameModeStats] = {}
        for p in pages:
            gm: str = p[WikiFields.GAME_MODE]
            if gm not in by_game:
                by_game[gm] = GameModeStats(pages=0, chunks=0, tables=0)
            by_game[gm]["pages"] += 1
            by_game[gm]["chunks"] += p[WikiFields.CHUNK_COUNT]
            by_game[gm]["tables"] += p[WikiFields.TABLE_COUNT]
        for gm in sorted(by_game):
            s = by_game[gm]
            logger.info(
                "  %-5s: %d pages, %d chunks, %d tables",
                gm, s["pages"], s["chunks"], s["tables"],
            )

    if tables:
        total_rows = sum(t[WikiFields.ROW_COUNT] for t in tables)
        logger.info("  Total table rows: %d", total_rows)

    if chunks:
        total_chars = sum(len(c[WikiFields.CONTENT]) for c in chunks)
        avg = total_chars // len(chunks)
        logger.info("  Total chunk chars: %d (avg %d)", total_chars, avg)

    # Sample documents
    if chunks:
        c = chunks[0]
        logger.info("  Sample chunk:")
        logger.info("    _id: %s", c[WikiFields.ID])
        logger.info("    [%s/%s] %s", c[WikiFields.GAME_MODE], c[WikiFields.CATEGORY], c[WikiFields.PAGE_TITLE])
        logger.info("    %s > %s", c[WikiFields.SECTION], c[WikiFields.SUB_SECTION])
        logger.info("    %s...", c[WikiFields.CONTENT][:80])

    if tables:
        t = tables[0]
        logger.info("  Sample table:")
        logger.info("    _id: %s", t[WikiFields.ID])
        logger.info("    [%s/%s] %s", t[WikiFields.GAME_MODE], t[WikiFields.CATEGORY], t[WikiFields.PAGE_NAME])
        logger.info("    headers: %s", t[WikiFields.HEADERS][:5])
        logger.info("    rows: %d, _search_text: %d chars", t[WikiFields.ROW_COUNT], len(t[WikiFields.SEARCH_TEXT]))


# ── MongoDB upload ─────────────────────────────────────────────────────────────
async def upload_to_mongo(
    chunks: list[ChunkDocument],
    tables: list[TableDocument],
    pages: list[PageDocument],
    clear: bool = False,
) -> None:
    """Upsert all documents to MongoDB."""
    db = MongoDB.get_db()
    col_chunks = db[CollectionNames.CHUNKS]
    col_tables = db[CollectionNames.TABLES]
    col_pages = db[CollectionNames.PAGES]

    # Clear if requested
    if clear:
        logger.warning("Dropping existing wiki collections...")
        for col in (col_chunks, col_tables, col_pages):
            result = await col.delete_many({})
            logger.info("  %s: deleted %d docs", col.name, result.deleted_count)

    # Bulk upsert using ReplaceOne (idempotent re-runs)
    async def bulk_upsert(col, docs: list[dict[str, Any]], label: str) -> None:
        if not docs:
            return
        total = 0
        for i in range(0, len(docs), _BATCH_SIZE):
            batch = docs[i : i + _BATCH_SIZE]
            ops = [
                ReplaceOne({WikiFields.ID: doc[WikiFields.ID]}, doc, upsert=True)
                for doc in batch
            ]
            result = await col.bulk_write(ops, ordered=False)
            total += result.upserted_count + result.modified_count
        logger.info("  %s: upserted %d / %d docs", label, total, len(docs))

    logger.info("Uploading to MongoDB...")
    t0 = time.time()
    await bulk_upsert(col_chunks, chunks, CollectionNames.CHUNKS)
    await bulk_upsert(col_tables, tables, CollectionNames.TABLES)
    await bulk_upsert(col_pages, pages, CollectionNames.PAGES)
    logger.info("Upload complete in %.1fs", time.time() - t0)

    # Create text indexes
    logger.info("Ensuring text indexes...")
    try:
        await col_chunks.create_index(
            [("content", "text"), ("page_title", "text"), ("section", "text")],
            name="text_search",
            default_language="english",
        )
        logger.info("  %s: text index on content+page_title+section", CollectionNames.CHUNKS)
    except pymongo.errors.OperationFailure as e:
        logger.warning("  %s: text index skipped (%s)", CollectionNames.CHUNKS, e)

    try:
        await col_tables.create_index(
            [("_search_text", "text"), ("page_name", "text")],
            name="text_search",
            default_language="english",
        )
        logger.info("  %s: text index on _search_text+page_name", CollectionNames.TABLES)
    except pymongo.errors.OperationFailure as e:
        logger.warning("  %s: text index skipped (%s)", CollectionNames.TABLES, e)

    # Regular indexes for filtered queries
    try:
        await col_chunks.create_index([(WikiFields.GAME_MODE, 1), (WikiFields.CATEGORY, 1)])
        await col_tables.create_index([(WikiFields.GAME_MODE, 1), (WikiFields.CATEGORY, 1)])
        await col_pages.create_index([(WikiFields.GAME_MODE, 1), (WikiFields.CATEGORY, 1)])
        logger.info("  Filter indexes on game_mode+category ensured")
    except pymongo.errors.OperationFailure as e:
        logger.error("  Filter index error: %s", e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload wiki_raw data to MongoDB")
    parser.add_argument("--dry-run", action="store_true", help="Load and show stats only, no upload")
    parser.add_argument("--clear", action="store_true", help="Drop existing wiki collections before upload")
    parser.add_argument("--stats", action="store_true", help="Print stats from wiki_raw and exit")
    args = parser.parse_args()

    sep = "=" * 60
    logger.info(sep)
    logger.info("  Wiki → MongoDB ETL")
    logger.info("  Source: %s", WIKI_RAW_DIR)
    logger.info("  Mode:   %s", "DRY RUN" if args.dry_run else "UPLOAD")
    if args.clear:
        logger.info("  Clear:  YES — will drop existing data first")
    logger.info(sep)

    t0 = time.time()
    chunks, tables, pages = load_all_documents()
    logger.info("Loaded in %.1fs", time.time() - t0)

    log_stats(chunks, tables, pages)

    if args.stats or args.dry_run:
        logger.info("[DRY RUN] No data uploaded.")
        return

    # Upload
    asyncio.run(upload_to_mongo(chunks, tables, pages, clear=args.clear))


if __name__ == "__main__":
    main()
