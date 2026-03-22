"""
Arks-Visiphone Wiki Scraper

Supports 2 scraping methods:
1. Static (BeautifulSoup): Fast, lightweight - used for pure HTML pages.
2. Dynamic (Selenium): Slower but can handle JS-rendered content.

Scraped data is converted to clean Markdown and saved to the
data/storage/ directory to prepare for the Embedding step.
"""

import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# Wiki Base URL
WIKI_BASE = "https://pso2na.arks-visiphone.com/wiki"

# Directory to store scraped data
STORAGE_DIR = Path(__file__).parent.parent / "storage" / "wiki_raw"


def _ensure_storage():
    """Create storage directory if it doesn't exist."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. Static Scraper (BeautifulSoup) - For pure HTML pages
# ---------------------------------------------------------------------------

def scrape_static(page_path: str) -> str | None:
    """Scrape a Wiki page using raw HTTP requests.

    Args:
        page_path: Path after /wiki/ (e.g.: "Hunter" -> /wiki/Hunter).

    Returns:
        Markdown content or None if error.
    """
    url = f"{WIKI_BASE}/{page_path}"
    print(f"[Scraper/Static] Scraping: {url}")

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[Scraper/Static] Connection error: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Get the main content of the Wiki (exclude sidebar, header, footer)
    content_div = soup.find("div", {"id": "mw-content-text"})
    if not content_div:
        print(f"[Scraper/Static] Main content not found at {url}")
        return None

    # Remove unnecessary parts
    for tag in content_div.find_all(["script", "style", "nav"]):
        tag.decompose()

    # Convert HTML -> Clean Markdown
    markdown_text = md(str(content_div), heading_style="ATX", strip=["img"])
    return markdown_text.strip()


# ---------------------------------------------------------------------------
# 2. Dynamic Scraper (Selenium) - For JavaScript-rendered pages
# ---------------------------------------------------------------------------

def scrape_dynamic(page_path: str) -> str | None:
    """Scrape a Wiki page containing JS content using Selenium.

    Args:
        page_path: Path after /wiki/ (e.g.: "NGS_Weapons_List").

    Returns:
        Markdown content or None if error.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    url = f"{WIKI_BASE}/{page_path}"
    print(f"[Scraper/Selenium] Scraping: {url}")

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = None
    try:
        # pylint: disable=not-callable
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options,
        )
        driver.get(url)
        time.sleep(3)  # Wait for JS to render

        soup = BeautifulSoup(driver.page_source, "html.parser")
        content_div = soup.find("div", {"id": "mw-content-text"})

        if not content_div:
            print("[Scraper/Selenium] Main content not found.")
            return None

        for tag in content_div.find_all(["script", "style", "nav"]):
            tag.decompose()

        markdown_text = md(str(content_div), heading_style="ATX", strip=["img"])
        return markdown_text.strip()

    except Exception as e: # pylint: disable=broad-exception-caught
        print(f"[Scraper/Selenium] Error: {e}")
        return None
    finally:
        if driver:
            driver.quit()


# ---------------------------------------------------------------------------
# 3. Save & Batch
# ---------------------------------------------------------------------------

def save_page(page_path: str, content: str):
    """Save Markdown content to file.

    Args:
        page_path: Wiki page name (used as filename).
        content: Markdown content.
    """
    _ensure_storage()
    filename = page_path.replace("/", "_").replace(" ", "_") + ".md"
    filepath = STORAGE_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    print(f"[Scraper] Saved: {filepath}")


def scrape_and_save(page_path: str, use_selenium: bool = False):
    """Scrape 1 Wiki page and save to storage.

    Args:
        page_path: Wiki path.
        use_selenium: True if page requires JS rendering.
    """
    if use_selenium:
        content = scrape_dynamic(page_path)
    else:
        content = scrape_static(page_path)

    if content:
        save_page(page_path, content)
    else:
        print(f"[Scraper] Skipping page: {page_path} (no content)")


# ---------------------------------------------------------------------------
# 4. List of pages to scrape (Expand gradually)
# ---------------------------------------------------------------------------

# Priority pages for MVP (Basic NGS Classes & Weapons)
NGS_PRIORITY_PAGES = [
    "Hunter",
    "Fighter",
    "Ranger",
    "Gunner",
    "Force",
    "Techter",
    "Braver",
    "Bouncer",
    "Waker",
    "Slayer",
]


if __name__ == "__main__":
    print("=== Starting Wiki Data Scrape (Static) ===")
    for page in NGS_PRIORITY_PAGES:
        scrape_and_save(page, use_selenium=False)
        time.sleep(1)  # Be polite: do not spam the Wiki server
    print("=== Completed ===")
