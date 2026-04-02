"""
Test Script: MCP vs MediaWiki API — Connectivity & Quality Comparison

Tests:
1. MediaWiki API (action=parse) — direct, no Chrome
2. MCP Bridge (Chrome DevTools) — current approach
3. Compare output quality and reliability

Usage:
    uv run python tests/test_wiki_access.py
"""

import asyncio
import time
import requests
from pathlib import Path

# ---------------------------------------------------------------------------
# Test 1: MediaWiki API (Direct HTTP — no Chrome needed)
# ---------------------------------------------------------------------------

API_BASE = "https://pso2na.arks-visiphone.com/api.php"
HEADERS = {"User-Agent": "PSO2-Bot-Test/1.0 (research only)"}

def test_mediawiki_api(page_slug: str) -> dict:
    """Fetch a wiki page via the public MediaWiki API."""
    print(f"\n{'='*60}")
    print(f"[TEST] MediaWiki API — page: {page_slug}")
    print(f"{'='*60}")
    
    start = time.time()
    try:
        resp = requests.get(
            API_BASE,
            params={
                "action": "parse",
                "page": page_slug,
                "format": "json",
                "prop": "text|categories|displaytitle",
            },
            headers=HEADERS,
            timeout=15,
        )
        elapsed = time.time() - start
        
        if resp.status_code != 200:
            print(f"  [FAIL] HTTP {resp.status_code}")
            return {"status": "FAIL", "error": f"HTTP {resp.status_code}", "time": elapsed}
        
        data = resp.json()
        
        if "error" in data:
            error_msg = data["error"].get("info", "Unknown error")
            print(f"  [FAIL] API error: {error_msg}")
            return {"status": "FAIL", "error": error_msg, "time": elapsed}
        
        parse = data.get("parse", {})
        title = parse.get("displaytitle", "Unknown")
        html = parse.get("text", {}).get("*", "")
        categories = [c["*"] for c in parse.get("categories", [])]
        
        # Convert HTML to plain text for comparison
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove nav/noise elements
        for tag in soup.find_all(["script", "style", "nav"]):
            tag.decompose()
        for el in soup.find_all("span", class_="mw-editsection"):
            el.decompose()
        for el in soup.find_all("div", id="toc"):
            el.decompose()
        
        text = soup.get_text(separator="\n", strip=True)
        
        print(f"  [OK] Title: {title}")
        print(f"  [OK] HTML length: {len(html)} chars")
        print(f"  [OK] Text length: {len(text)} chars")
        print(f"  [OK] Categories: {categories[:5]}")
        print(f"  [OK] Time: {elapsed:.2f}s")
        print(f"  [OK] Preview (first 300 chars):")
        print(f"       {text[:300]}...")
        
        return {
            "status": "OK",
            "title": title,
            "html_len": len(html),
            "text_len": len(text),
            "categories": categories,
            "time": elapsed,
            "text_preview": text[:500],
        }
        
    except requests.exceptions.Timeout:
        elapsed = time.time() - start
        print(f"  [FAIL] Timeout after {elapsed:.2f}s")
        return {"status": "FAIL", "error": "Timeout", "time": elapsed}
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [FAIL] {type(e).__name__}: {e}")
        return {"status": "FAIL", "error": str(e), "time": elapsed}


# ---------------------------------------------------------------------------
# Test 2: MCP Bridge (Chrome DevTools)
# ---------------------------------------------------------------------------

async def test_mcp_bridge(page_slug: str) -> dict:
    """Test the MCP Bridge with Chrome DevTools."""
    print(f"\n{'='*60}")
    print(f"[TEST] MCP Bridge (Chrome DevTools) — page: {page_slug}")
    print(f"{'='*60}")
    
    try:
        from core.mcp.mcp_client import MCPBridge
    except ImportError as e:
        print(f"  [SKIP] Cannot import MCPBridge: {e}")
        return {"status": "SKIP", "error": str(e), "time": 0}
    
    bridge = MCPBridge()
    start = time.time()
    
    try:
        print("  [INFO] Starting Chrome DevTools MCP server...")
        await bridge.start()
        print(f"  [OK] MCP started in {time.time() - start:.2f}s")
        
        url = f"https://pso2na.arks-visiphone.com/wiki/{page_slug}"
        print(f"  [INFO] Fetching: {url}")
        
        fetch_start = time.time()
        content = await bridge.fetch_url(url)
        fetch_time = time.time() - fetch_start
        
        total_time = time.time() - start
        
        print(f"  [OK] Content length: {len(content)} chars")
        print(f"  [OK] Fetch time: {fetch_time:.2f}s")
        print(f"  [OK] Total time (incl. Chrome startup): {total_time:.2f}s")
        print(f"  [OK] Preview (first 300 chars):")
        print(f"       {content[:300]}...")
        
        return {
            "status": "OK",
            "text_len": len(content),
            "fetch_time": fetch_time,
            "total_time": total_time,
            "text_preview": content[:500],
        }
        
    except Exception as e:
        total_time = time.time() - start
        print(f"  [FAIL] {type(e).__name__}: {e}")
        return {"status": "FAIL", "error": str(e), "time": total_time}
    finally:
        await bridge.close()


# ---------------------------------------------------------------------------
# Test 3: Slug Resolution (Gemini)
# ---------------------------------------------------------------------------

async def test_slug_resolution(query: str, game_version: str) -> dict:
    """Test the Gemini-based slug resolver."""
    print(f"\n{'='*60}")
    print(f"[TEST] Slug Resolution — query: '{query}' game: {game_version}")
    print(f"{'='*60}")
    
    try:
        from core.mcp.mcp_client import MCPBridge
    except ImportError as e:
        print(f"  [SKIP] Cannot import MCPBridge: {e}")
        return {"status": "SKIP", "error": str(e)}
    
    bridge = MCPBridge()
    start = time.time()
    
    try:
        slug = await bridge._resolve_wiki_slug(query, game_version)
        elapsed = time.time() - start
        
        if slug:
            print(f"  [OK] Resolved slug: {slug}")
            print(f"  [OK] Full URL: https://pso2na.arks-visiphone.com/wiki/{slug}")
        else:
            print(f"  [FAIL] Could not resolve slug")
        
        print(f"  [OK] Time: {elapsed:.2f}s")
        return {"status": "OK" if slug else "FAIL", "slug": slug, "time": elapsed}
        
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [FAIL] {type(e).__name__}: {e}")
        return {"status": "FAIL", "error": str(e), "time": elapsed}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Wiki Access Test Suite")
    print("  Comparing: MediaWiki API vs MCP (Chrome DevTools)")
    print("=" * 60)
    
    test_pages = [
        ("Portal:New_Genesis/Ranger", "NGS Ranger class page"),
        ("Portal:New_Genesis/Slayer", "NGS Slayer class page"),
        ("Hunter", "PSO2 Classic Hunter page"),
    ]
    
    # ---- Test MediaWiki API ----
    print("\n\n" + "#" * 60)
    print("# PART 1: MediaWiki API (Direct HTTP)")
    print("#" * 60)
    
    api_results = {}
    for slug, desc in test_pages:
        result = test_mediawiki_api(slug)
        api_results[slug] = result
    
    # ---- Test Slug Resolution ----
    print("\n\n" + "#" * 60)
    print("# PART 2: Slug Resolution (Gemini)")
    print("#" * 60)
    
    slug_queries = [
        ("What skills does Ranger have?", "ngs"),
        ("Tell me about Slayer class", "ngs"),
        ("How to play Hunter?", "pso2"),
        ("Slayer skills in base PSO2", "pso2"),  # Contradiction test!
    ]
    
    slug_results = {}
    for query, game in slug_queries:
        result = asyncio.run(test_slug_resolution(query, game))
        slug_results[query] = result
    
    # ---- Test MCP Bridge ----
    print("\n\n" + "#" * 60)
    print("# PART 3: MCP Bridge (Chrome DevTools)")
    print("#" * 60)
    
    mcp_results = {}
    # Only test one page with MCP (Chrome startup is slow)
    test_slug = "Portal:New_Genesis/Ranger"
    mcp_results[test_slug] = asyncio.run(test_mcp_bridge(test_slug))
    
    # ---- Summary ----
    print("\n\n" + "=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    
    print("\n--- MediaWiki API ---")
    for slug, r in api_results.items():
        status = r["status"]
        time_s = r.get("time", 0)
        text_len = r.get("text_len", 0)
        print(f"  [{status}] {slug}: {text_len} chars in {time_s:.2f}s")
    
    print("\n--- Slug Resolution ---")
    for query, r in slug_results.items():
        status = r["status"]
        slug = r.get("slug", "N/A")
        print(f"  [{status}] '{query}' → {slug}")
    
    print("\n--- MCP Bridge ---")
    for slug, r in mcp_results.items():
        status = r["status"]
        time_s = r.get("total_time", r.get("time", 0))
        text_len = r.get("text_len", 0)
        error = r.get("error", "")
        if status == "OK":
            print(f"  [{status}] {slug}: {text_len} chars in {time_s:.2f}s")
        else:
            print(f"  [{status}] {slug}: {error} in {time_s:.2f}s")


if __name__ == "__main__":
    main()
