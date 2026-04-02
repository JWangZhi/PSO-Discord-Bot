"""Wiki scraper-specific settings."""

import os
from pathlib import Path


SCRAPER_BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "scrapers"
SCRAPER_STORAGE_ROOT = SCRAPER_BASE_DIR.parent / "storage"

API_BASE = os.getenv("WIKI_API_BASE", "https://pso2na.arks-visiphone.com/api.php")
WIKI_BASE = os.getenv("WIKI_BASE_URL", "https://pso2na.arks-visiphone.com/wiki")
SCRAPER_USER_AGENT = os.getenv("SCRAPER_USER_AGENT", "PSO2-Bot-Scraper/3.0 (research only)")
HEADERS = {"User-Agent": SCRAPER_USER_AGENT}

STORAGE_DIR = SCRAPER_STORAGE_ROOT / "wiki_raw"
CACHE_DIR = SCRAPER_STORAGE_ROOT / "cache"
MANIFEST_FILE = SCRAPER_BASE_DIR / "wiki_scraper_manifest.json"

REQUEST_DELAY = float(os.getenv("WIKI_REQUEST_DELAY", "1.2"))
CACHE_TTL_H = int(os.getenv("WIKI_CACHE_TTL_H", "24"))
MIN_CHUNK_LEN = int(os.getenv("WIKI_MIN_CHUNK_LEN", "30"))
BATCH_SIZE = int(os.getenv("WIKI_BATCH_SIZE", "50"))

NAV_TABLE_CLASS_HINTS = {
    "navbox",
    "vertical-navbox",
    "portalbox",
    "metadata",
    "infobox",
    "sidebar",
    "toc",
}

NAV_CAPTION_HINTS = {
    "navigation",
    "portal",
    "see also",
    "related",
    "class list",
    "classes",
}


# Official PSO2 Players site ingestion
PLAYERS_BASE_URL = os.getenv("PLAYERS_BASE_URL", "https://pso2.com/players/")
PLAYERS_ALLOWED_PATH_PREFIX = os.getenv("PLAYERS_ALLOWED_PATH_PREFIX", "/players/")
PLAYERS_MAX_PAGES = int(os.getenv("PLAYERS_MAX_PAGES", "40"))
PLAYERS_FETCH_TIMEOUT = int(os.getenv("PLAYERS_FETCH_TIMEOUT", "20"))
PLAYERS_REQUEST_DELAY = float(os.getenv("PLAYERS_REQUEST_DELAY", "0.6"))
PLAYERS_MIN_TEXT_LEN = int(os.getenv("PLAYERS_MIN_TEXT_LEN", "160"))
PLAYERS_CHUNK_SIZE = int(os.getenv("PLAYERS_CHUNK_SIZE", "1800"))
PLAYERS_CHUNK_OVERLAP = int(os.getenv("PLAYERS_CHUNK_OVERLAP", "220"))
PLAYERS_GAME_MODE = os.getenv("PLAYERS_GAME_MODE", "NGS")
PLAYERS_CATEGORY = os.getenv("PLAYERS_CATEGORY", "official")
