"""
Arks-Visiphone Wiki Scraper — V3.0 (MediaWiki API + Pandas)

Architecture based on data_scrape_strategy.md V3 (Locked 2025-03-23).

Usage:
  python wiki_scraper.py              # scrape all (with incremental check)
  python wiki_scraper.py --fast       # scrape all, skip validation/incremental
  python wiki_scraper.py ngs          # NGS only
  python wiki_scraper.py pso2         # PSO2 only
  python wiki_scraper.py classes      # section "classes" in both games
  python wiki_scraper.py ngs:classes  # NGS classes only
  python wiki_scraper.py ngs:weapons  # NGS weapons only
"""

import sys
import re
import time
import json
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import markdownify as md
from settings.scraper import (
    API_BASE,
    WIKI_BASE,
    HEADERS,
    STORAGE_DIR,
    CACHE_DIR,
    MANIFEST_FILE,
    REQUEST_DELAY,
    CACHE_TTL_H,
    MIN_CHUNK_LEN,
    BATCH_SIZE,
    NAV_TABLE_CLASS_HINTS,
    NAV_CAPTION_HINTS,
)

SKIP_VALIDATION = "--fast" in sys.argv


# ---------------------------------------------------------------------------
# 1. Helpers
# ---------------------------------------------------------------------------

def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def _safe_filename(page_path: str) -> str:
    return re.sub(r"[^\w\-]", "_", page_path)


def _page_url(page_path: str) -> str:
    return f"{WIKI_BASE}/{page_path}"


def _out_dir(game_mode: str, category: str) -> Path:
    d = STORAGE_DIR / game_mode.upper() / category
    _ensure_dir(d)
    return d


def _chunk_id(url: str, section: str, sub_section: str) -> str:
    """Deterministic hash for Pinecone deduplication (SHA-256, 32 chars)."""
    raw = f"{url}::{section}::{sub_section}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_nav_table(table: Tag) -> bool:
    """Heuristic detection for navigation-like tables that pollute text chunks."""
    if not table.attrs:
        return False
    class_tokens = {
        token.lower()
        for cls in (table.attrs.get("class") or [])
        for token in str(cls).split()
        if token
    }
    if class_tokens & NAV_TABLE_CLASS_HINTS:
        return True

    caption = table.find("caption")
    if caption:
        caption_text = caption.get_text(" ", strip=True).lower()
        if any(hint in caption_text for hint in NAV_CAPTION_HINTS):
            return True

    links = table.find_all("a")
    link_count = len(links)
    text = table.get_text(" ", strip=True)
    text_len = len(text)

    # High link density and lots of short link labels are common in nav boxes.
    if link_count >= 8 and text_len < 1800:
        short_anchor_count = sum(1 for a in links if len(a.get_text(" ", strip=True)) <= 30)
        if short_anchor_count / max(link_count, 1) >= 0.7:
            return True

    if link_count >= 10:
        anchor_text_len = sum(len(a.get_text(" ", strip=True)) for a in links)
        link_density = anchor_text_len / max(text_len, 1)
        if link_density >= 0.6:
            return True

    return False


def _is_skill_table(table: Tag) -> bool:
    """Detect skill data tables (have class table-responsive-md + wikitable)."""
    if not table.attrs:
        return False
    classes = {str(c).lower() for c in (table.attrs.get("class") or [])}
    return "table-responsive-md" in classes and "wikitable" in classes


def _extract_skill_from_table(table: Tag) -> dict | None:
    """Extract structured skill data from a single wiki skill table.

    Returns dict with: skill_name, description, restriction, prerequisite, stats
    Returns None if table doesn't match expected skill table format.
    """
    rows = table.find_all("tr")
    if len(rows) < 3:
        return None

    # Row 0: Skill name (first cell) + Description (second cell, usually colspan)
    first_row_cells = rows[0].find_all(["td", "th"])
    if not first_row_cells:
        return None

    skill_name = first_row_cells[0].get_text(strip=True)
    description = ""
    if len(first_row_cells) > 1:
        description = first_row_cells[1].get_text(strip=True)

    # Scan for restriction and prerequisite (colspan rows before stat rows)
    restriction = ""
    prerequisite = ""
    stat_start_row = 1

    for i in range(1, len(rows)):
        cells = rows[i].find_all(["td", "th"])
        # Full-width rows (colspan) contain restriction or prerequisite
        if len(cells) == 1 and cells[0].get("colspan"):
            text = cells[0].get_text(strip=True)
            if "Prerequisite" in text:
                prerequisite = text
            elif "Can only" in text or "only be used" in text:
                restriction = text
            stat_start_row = i + 1
        else:
            break

    # Remaining rows: key-value stat pairs
    # Skip header rows ("Effect | Skill Level" and "1 | 2 | 3...")
    stats = {}
    for row in rows[stat_start_row:]:
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            key = cells[0].get_text(strip=True)
            if key in ("Effect", ""):
                continue
            # Skip level-number rows (all cells are digits)
            values = [c.get_text(strip=True) for c in cells[1:] if c.get_text(strip=True)]
            if values and all(v.isdigit() for v in values):
                continue
            if values:
                stats[key] = values[0] if len(values) == 1 else " / ".join(values)

    if not skill_name:
        return None

    return {
        "skill_name": skill_name,
        "description": description,
        "restriction": restriction,
        "prerequisite": prerequisite,
        "stats": stats,
    }


def _skill_to_chunk_text(skill: dict) -> str:
    """Convert extracted skill dict into clean markdown for embedding."""
    lines = [f"## {skill['skill_name']}"]
    if skill["description"]:
        lines.append(skill["description"])
    if skill["restriction"]:
        lines.append(skill["restriction"])
    if skill["prerequisite"]:
        lines.append(skill["prerequisite"])
    if skill["stats"]:
        for key, val in skill["stats"].items():
            lines.append(f"- {key}: {val}")
    return "\n".join(lines)


def _remove_noise_tables_for_text(soup: BeautifulSoup) -> int:
    """Remove navigation and list-like tables before text chunk extraction."""
    removed = 0
    for table in soup.find_all("table"):
        if _is_nav_table(table):
            table.decompose()
            removed += 1
            continue

        # For text chunking, table-heavy content is handled by table extractor separately.
        if table.attrs and "wikitable" in {str(c).lower() for c in (table.attrs.get("class") or [])}:
            table.decompose()
            removed += 1

    return removed


# ---------------------------------------------------------------------------
# 2. MediaWiki API: Batch Validation + Incremental Check
# ---------------------------------------------------------------------------

def api_batch_check(page_paths: list[str]) -> dict[str, dict]:
    """
    Single API call to check existence + last revision timestamp
    for up to BATCH_SIZE pages.

    Returns: { page_path: { "exists": bool, "last_rev_ts": str|None } }
    """
    titles_str = "|".join(page_paths)
    params = {
        "action":  "query",
        "prop":    "info|revisions",
        "rvprop":  "timestamp",
        "titles":  titles_str,
        "format":  "json",
    }

    try:
        resp = requests.get(API_BASE, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [ERROR] API batch check failed: {e}")
        # On failure, assume all pages exist and need scraping
        return {p: {"exists": True, "last_rev_ts": None} for p in page_paths}

    # Build a normalized-title → original-path map
    normalized = data.get("query", {}).get("normalized", [])
    norm_map = {n["to"]: n["from"] for n in normalized}

    results = {}
    pages = data.get("query", {}).get("pages", {})

    for page_id, page_info in pages.items():
        title = page_info.get("title", "")
        # Map back to original page_path
        original = norm_map.get(title, title)

        if "missing" in page_info:
            results[original] = {"exists": False, "last_rev_ts": None}
        else:
            revs = page_info.get("revisions", [])
            ts = revs[0]["timestamp"] if revs else None
            results[original] = {"exists": True, "last_rev_ts": ts}

    # Fill in any pages not returned (edge case)
    for p in page_paths:
        if p not in results:
            results[p] = {"exists": True, "last_rev_ts": None}

    return results


def should_scrape(page_path: str, api_info: dict, game_mode: str, category: str) -> bool:
    """
    Decide whether to scrape based on API info and local meta.
    """
    if not api_info.get("exists", True):
        print(f"  [SKIP] Redlink (missing): {page_path}")
        return False

    if SKIP_VALIDATION:
        return True

    # Check local meta for last scrape time
    meta_file = _out_dir(game_mode, category) / (_safe_filename(page_path) + ".meta.json")
    if not meta_file.exists():
        return True  # Never scraped before

    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        local_ts = meta.get("scraped_at")
        remote_ts = api_info.get("last_rev_ts")

        if local_ts and remote_ts:
            # MediaWiki timestamps: "2025-03-20T08:30:00Z"
            local_dt = datetime.fromisoformat(local_ts.replace("Z", "+00:00"))
            remote_dt = datetime.fromisoformat(remote_ts.replace("Z", "+00:00"))
            if remote_dt <= local_dt:
                print(f"  [SKIP] No changes since last scrape: {page_path}")
                return False
    except Exception:
        pass  # If meta is corrupt, just re-scrape

    return True


# ---------------------------------------------------------------------------
# 3. Content Fetcher (action=parse + Local Cache)
# ---------------------------------------------------------------------------

def fetch_html(page_path: str) -> Optional[str]:
    """
    Fetch rendered HTML via MediaWiki action=parse API.
    Uses local cache with 24h TTL.
    """
    _ensure_dir(CACHE_DIR)
    cache_file = CACHE_DIR / (_safe_filename(page_path) + ".html")

    # Check cache
    if cache_file.exists():
        age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
        if age < timedelta(hours=CACHE_TTL_H):
            print(f"  [CACHE] Using cached HTML: {cache_file.name}")
            return cache_file.read_text(encoding="utf-8")

    # Fetch from API
    params = {
        "action": "parse",
        "page":   page_path,
        "prop":   "text",
        "format": "json",
    }

    try:
        resp = requests.get(API_BASE, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [ERROR] API parse failed for {page_path}: {e}")
        return None

    if "error" in data:
        print(f"  [ERROR] API error: {data['error'].get('info', 'unknown')}")
        return None

    html = data.get("parse", {}).get("text", {}).get("*", "")
    if not html:
        print(f"  [ERROR] Empty HTML from API: {page_path}")
        return None

    # Save to cache
    cache_file.write_text(html, encoding="utf-8")
    print(f"  [FETCHED] {page_path} ({len(html):,} bytes)")

    # Polite delay after real API call
    time.sleep(REQUEST_DELAY)
    return html


# ---------------------------------------------------------------------------
# 4. Table Extractor (BeautifulSoup)
# ---------------------------------------------------------------------------

def extract_tables(
    html: str,
    game_mode: str,
    category: str,
    page_path: str
) -> list[dict]:
    """
    Parse all wikitables using BeautifulSoup.
    Skips skill tables (handled separately in extract_text_chunks).
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for table_idx, table in enumerate(soup.find_all("table", class_="wikitable")):
        # Skip skill tables — they are handled by the skill extractor
        if _is_skill_table(table):
            continue
        # Skip nav tables
        if _is_nav_table(table):
            continue

        tr_tags = table.find_all("tr")
        if not tr_tags:
            continue

        # Extract headers from the first row
        header_row = tr_tags[0]
        headers = [
            re.sub(r"\s+", " ", cell.get_text(strip=True)).lower()
            for cell in header_row.find_all(["th", "td"])
        ]
        if not headers:
            continue

        # Extract data rows
        rows = []
        for tr in tr_tags[1:]:
            cells = tr.find_all(["td", "th"])
            values = [cell.get_text(strip=True) for cell in cells]
            if not values or all(v == values[0] for v in values):
                continue  # skip empty or sub-header rows
            # Pad or trim to match header length
            row_dict = {}
            for i, h in enumerate(headers):
                row_dict[h] = values[i] if i < len(values) else ""
            row_dict["_game_mode"]  = game_mode.upper()
            row_dict["_category"]   = category
            row_dict["_page"]       = page_path
            row_dict["_source_url"] = _page_url(page_path)
            rows.append(row_dict)

        if not rows:
            continue

        results.append({
            "table_index": table_idx,
            "headers":     headers,
            "row_count":   len(rows),
            "rows":        rows,
        })

    return results


# ---------------------------------------------------------------------------
# 5. Text Chunk Extractor
# ---------------------------------------------------------------------------

def extract_text_chunks(
    html: str,
    game_mode: str,
    category: str,
    page_path: str,
    page_title: str
) -> list[dict]:
    """
    Split content by h2/h3 headings into semantic chunks for RAG embedding.
    Skill tables are extracted as individual chunks before prose extraction.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Clean noise
    for tag in soup.find_all(["script", "style", "nav"]):
        tag.decompose()
    for span in soup.find_all("span", class_="mw-editsection"):
        span.decompose()
    for toc in soup.find_all("div", id="toc"):
        toc.decompose()

    # Unwrap the mw-parser-output wrapper that action=parse returns
    content_root = soup.find("div", class_="mw-parser-output")
    if not content_root:
        content_root = soup  # fallback to soup itself

    chunks: list[dict] = []
    url = _page_url(page_path)

    # Pass 1: Extract skills from skill tables (before removing them)
    skill_count = 0
    for table in content_root.find_all("table"):
        if _is_skill_table(table):
            skill = _extract_skill_from_table(table)
            if skill:
                text = _skill_to_chunk_text(skill)
                if len(text) >= MIN_CHUNK_LEN:
                    chunks.append({
                        "chunk_id":     _chunk_id(url, "Skills", skill["skill_name"]),
                        "game_mode":    game_mode.upper(),
                        "category":     category,
                        "page_title":   page_title,
                        "section":      "Skills",
                        "sub_section":  skill["skill_name"],
                        "content":      text,
                        "url":          url,
                        "last_scraped": _now_iso(),
                    })
                    skill_count += 1
            # Remove the table so it doesn't pollute prose chunks
            table.decompose()
        elif _is_nav_table(table):
            table.decompose()

    if skill_count:
        print(f"  [SKILLS] Extracted {skill_count} skill chunk(s)")

    # Remove remaining wikitables (handled by table extractor separately)
    for table in content_root.find_all("table"):
        if table.attrs and "wikitable" in {str(c).lower() for c in (table.attrs.get("class") or [])}:
            table.decompose()

    # Pass 2: Extract remaining prose by h2/h3 headings
    current_h2: str = "Introduction"
    current_h3: str = "Overview"
    buffer: list     = []

    def flush(h2: str, h3: str, buf: list):
        if not buf:
            return
        html_block = "".join(str(t) for t in buf)
        text = md(html_block, heading_style="ATX", strip=["img"]).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) < MIN_CHUNK_LEN:
            return
        chunks.append({
            "chunk_id":     _chunk_id(url, h2, h3),
            "game_mode":    game_mode.upper(),
            "category":     category,
            "page_title":   page_title,
            "section":      h2,
            "sub_section":  h3,
            "content":      text,
            "url":          url,
            "last_scraped": _now_iso(),
        })

    for element in content_root.children:
        if isinstance(element, NavigableString):
            continue
        if not isinstance(element, Tag):
            continue

        tag_name = element.name

        if tag_name == "h2":
            flush(current_h2, current_h3, buffer)
            buffer     = []
            current_h2 = element.get_text(strip=True)
            current_h3 = ""

        elif tag_name == "h3":
            flush(current_h2, current_h3, buffer)
            buffer     = []
            current_h3 = element.get_text(strip=True)

        else:
            buffer.append(element)

    # Flush remaining
    flush(current_h2, current_h3, buffer)

    return chunks


# ---------------------------------------------------------------------------
# 6. Savers
# ---------------------------------------------------------------------------

def save_tables(page_path: str, game_mode: str, category: str, tables: list[dict]):
    filepath = _out_dir(game_mode, category) / (_safe_filename(page_path) + ".tables.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(tables, f, ensure_ascii=False, indent=2, default=str)
    total_rows = sum(t["row_count"] for t in tables)
    print(f"  [SAVED] tables   -> {filepath.name} ({len(tables)} tables, {total_rows} rows)")


def save_chunks(page_path: str, game_mode: str, category: str, chunks: list[dict]):
    filepath = _out_dir(game_mode, category) / (_safe_filename(page_path) + ".chunks.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"  [SAVED] chunks   -> {filepath.name} ({len(chunks)} chunks)")


def save_markdown(page_path: str, game_mode: str, category: str, html: str):
    filepath = _out_dir(game_mode, category) / (_safe_filename(page_path) + ".md")
    text = md(html, heading_style="ATX", strip=["img"]).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    filepath.write_text(text, encoding="utf-8")
    print(f"  [SAVED] markdown -> {filepath.name}")


def save_meta(
    page_path: str,
    game_mode: str,
    category: str,
    scrape_type: str,
    n_tables: int,
    n_chunks: int,
    lastrevid: Optional[str] = None,
):
    filepath = _out_dir(game_mode, category) / (_safe_filename(page_path) + ".meta.json")
    meta = {
        "page_path":   page_path,
        "url":         _page_url(page_path),
        "game_mode":   game_mode.upper(),
        "category":    category,
        "scrape_type": scrape_type,
        "n_tables":    n_tables,
        "n_chunks":    n_chunks,
        "scraped_at":  _now_iso(),
        "lastrevid":   lastrevid,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 7. Core: Scrape 1 page (full pipeline)
# ---------------------------------------------------------------------------

def scrape_page(
    page_path: str,
    game_mode: str,
    category: str,
    scrape_type: str,
    last_rev_ts: Optional[str] = None,
):
    """Full pipeline: fetch -> extract tables + chunks -> save files."""

    print(f"\n-> [{game_mode.upper()}][{category}][{scrape_type}] {page_path}")

    # 1. Fetch HTML via API (with cache)
    html = fetch_html(page_path)
    if html is None:
        return

    # 2. Page title
    page_title = page_path.split("/")[-1].replace("_", " ")

    n_tables = 0
    n_chunks = 0

    # 3. Extract tables
    if scrape_type in ("structured_table", "mixed"):
        tables = extract_tables(html, game_mode, category, page_path)
        if tables:
            save_tables(page_path, game_mode, category, tables)
            n_tables = len(tables)
        elif scrape_type == "structured_table":
            print(f"  [WARN] No wikitable found: {page_path}")

    # 4. Extract text chunks
    if scrape_type in ("text_document", "mixed"):
        chunks = extract_text_chunks(html, game_mode, category, page_path, page_title)
        if chunks:
            save_chunks(page_path, game_mode, category, chunks)
            n_chunks = len(chunks)

    # 5. Save raw markdown (backup)
    save_markdown(page_path, game_mode, category, html)

    # 6. Save meta (with lastrevid for incremental updates)
    save_meta(page_path, game_mode, category, scrape_type, n_tables, n_chunks, last_rev_ts)


# ---------------------------------------------------------------------------
# 8. Manifest loader
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    if not MANIFEST_FILE.exists():
        raise FileNotFoundError(f"Manifest not found: {MANIFEST_FILE}")
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 9. Main runner
# ---------------------------------------------------------------------------

def run(batch: str = "all"):
    """
    batch:
      "all"          -> scrape everything
      "ngs"          -> only NGS
      "pso2"         -> only PSO2
      "classes"      -> section "classes" in both games
      "ngs:classes"  -> section "classes" in NGS only
    """
    print("=" * 60)
    print("  PSO2/NGS Wiki Scraper — V3.0 (MediaWiki API)")
    print(f"  Batch     : {batch}")
    print(f"  Fast mode : {SKIP_VALIDATION}")
    print(f"  Output    : {STORAGE_DIR}")
    print("=" * 60)

    manifest = load_manifest()

    # Parse batch filter
    batch_game    = None
    batch_section = None

    if batch != "all":
        if ":" in batch:
            parts         = batch.split(":", 1)
            batch_game    = parts[0].lower()
            batch_section = parts[1].lower()
        elif batch in ("ngs", "pso2"):
            batch_game = batch.lower()
        else:
            batch_section = batch.lower()

    total = 0
    done  = 0
    skip  = 0

    for game_mode, sections in manifest.items():

        if batch_game and game_mode != batch_game:
            continue

        for section_name, section_data in sections.items():

            if batch_section and section_name != batch_section:
                continue

            scrape_type = section_data.get("scrape_type", "mixed")
            category    = section_data.get("category", "general")
            pages       = section_data.get("pages", [])

            print(f"\n{'─' * 55}")
            print(
                f"  [{game_mode.upper()}] {section_name} "
                f"| type={scrape_type} | cat={category} | {len(pages)} pages"
            )
            print(f"{'─' * 55}")

            # Step 1: Batch validation + incremental check via API
            api_results = {}
            if not SKIP_VALIDATION:
                for i in range(0, len(pages), BATCH_SIZE):
                    batch_pages = pages[i:i + BATCH_SIZE]
                    batch_result = api_batch_check(batch_pages)
                    api_results.update(batch_result)
                    if i + BATCH_SIZE < len(pages):
                        time.sleep(REQUEST_DELAY)

            # Step 2: Scrape each page
            for page_path in pages:
                total += 1

                # Check with API results (if validation was done)
                api_info = api_results.get(page_path, {"exists": True, "last_rev_ts": None})

                if not SKIP_VALIDATION and not should_scrape(page_path, api_info, game_mode, category):
                    skip += 1
                    continue

                try:
                    scrape_page(
                        page_path,
                        game_mode,
                        category,
                        scrape_type,
                        last_rev_ts=api_info.get("last_rev_ts"),
                    )
                    done += 1
                except KeyboardInterrupt:
                    print("\n[INTERRUPTED] Stopping early.")
                    _print_summary(total, done, skip)
                    sys.exit(0)
                except Exception as e:
                    print(f"  [ERROR] Unhandled: {page_path} — {e}")
                    skip += 1

    _print_summary(total, done, skip)


def _print_summary(total: int, done: int, skip: int):
    print("\n" + "=" * 60)
    print(f"  Scraped  : {done}/{total} pages")
    print(f"  Skipped  : {skip}")
    print(f"  Output   : {STORAGE_DIR}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# 10. Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Filter out --fast from args for batch parsing
    args = [a for a in sys.argv[1:] if a != "--fast"]
    batch_arg = args[0] if args else "all"
    run(batch=batch_arg)