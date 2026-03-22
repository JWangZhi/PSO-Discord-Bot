"""
Phashion Scraper

Responsible for scraping Fashion catalogs (e.g., Arks-Visiphone).
Automatically discovers fashion categories (Basewear, Hairstyles, etc.) from the 
Fashion Directory, extracts structures, passes images to VisionAgent, and uploads
the vector embeddings to Pinecone.
"""

import sys
import os
import json
import time
import hashlib
import requests
from bs4 import BeautifulSoup
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.agents.vision_agent import VisionAgent
from data.rag.phashion_matcher import PhashionMatcher
import config
from pymongo import MongoClient

class PhashionScraper:
    def __init__(self):
        self.vision_agent = VisionAgent()
        self.matcher = PhashionMatcher()
        
        # Synchronous MongoDB Client for the scraper
        self.mongo_client = MongoClient(config.MONGODB_URI)
        self.mongo_db = self.mongo_client["pso2_bot"]
        self.mongo_col = self.mongo_db["phashion_items"]
        
    def get_fashion_category_urls(self, base_url: str = "https://pso2na.arks-visiphone.com/wiki/Portal:New_Genesis/Fashion") -> list[str]:
        """
        Extract all fashion sub-categories (Basewear, Hairstyles, Eyes, etc.) from the 'Fashion Directory' table.
        """
        urls = []
        try:
            print(f"[PhashionScraper] Discovering categories from: {base_url}")
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(base_url, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Find the Fashion Directory table by looking for its title or specific structure
            tables = soup.find_all("table")
            for table in tables:
                if "Fashion Directory" in table.text:
                    links = table.find_all("a")
                    for link in links:
                        href = link.get("href")
                        if href and "/wiki/Portal:New_Genesis/" in href:
                            full_url = "https://pso2na.arks-visiphone.com" + href
                            # Filter out non-category links
                            if "Change_to_Simplified_View" not in href and full_url not in urls:
                                urls.append(full_url)
                    break # Stop after finding the directory table
            
            print(f"[PhashionScraper] Discovered {len(urls)} category URLs.")
            return urls
        except Exception as e:
            print(f"[PhashionScraper] Error discovering categories: {e}")
            return []

    def download_image_bytes(self, url: str) -> bytes | None:
        """Download an image from a URL and return its bytes."""
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.content
        except Exception as e:
            print(f"[PhashionScraper] Error downloading image {url}: {e}")
        return None

    def auto_tag_item(self, item_name: str, image_url: str) -> dict:
        """Download image and use VisionAgent to generate structural tags."""
        print(f"[PhashionScraper] Processing: {item_name}")
        img_bytes = self.download_image_bytes(image_url)
        
        if not img_bytes:
            print(f"  -> Failed to download image for {item_name}")
            return {"error": "Image download failed"}
            
        tags = self.vision_agent.analyze_outfit_from_bytes(img_bytes)
        time.sleep(2) # Rate limiting protection
        return tags

    def embed_and_upload(self, item_name: str, img_url: str, tags: dict):
        """Convert tags to vector and upload to Pinecone."""
        if "error" in tags:
            return False
            
        vector = self.matcher.embed_tags(tags)
        if not vector:
            return False
            
        print(f"[PhashionScraper] Uploading {item_name} to MongoDB & Pinecone...")
        
        # 1. MongoDB Upload (Raw Data + Proof)
        from datetime import datetime, timezone
        item_data = {
            "name": item_name,
            "thumbnail": img_url,
            "tags": tags,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        try:
            self.mongo_col.update_one(
                {"name": item_name},
                {"$set": item_data},
                upsert=True
            )
        except Exception as e:
            print(f"[PhashionScraper] MongoDB Upsert Error: {e}")
            
        # 2. Pinecone Upload (Vector Search)
        if not self.matcher._index:
            print("[PhashionScraper] Error: Could not connect to pso2-phashion Pinecone index.")
            return False
            
        metadata = {
            "name": item_name,
            "thumbnail": img_url,
            "gender_focus": tags.get("gender_focus", "Unknown"),
            "style_theme": tags.get("style_theme", "Unknown")
        }
        struct_tags = tags.get("structural_tags", [])
        if struct_tags:
            metadata["structural"] = ", ".join(struct_tags)
            
        item_id = hashlib.md5(item_name.encode('utf-8')).hexdigest()
        
        try:
            self.matcher._index.upsert(vectors=[
                {"id": item_id, "values": vector, "metadata": metadata}
            ])
            return True
        except Exception as e:
            print(f"[PhashionScraper] Pinecone Upsert Error: {e}")
            return False

    def scrape_category(self, url: str, test_limit: int = 5):
        """
        Scrape Fashion items from a single category page using dynamic headers.
        """
        results = []
        print(f"\n[PhashionScraper] Scraping category: {url}")
        
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"[PhashionScraper] Connection error: {e}")
            return results

        soup = BeautifulSoup(response.text, "html.parser")
        tables = soup.find_all("table", class_="wikitable")
        if not tables:
            print("[PhashionScraper] No wikitables found on this page.")
            return results
            
        count = 0
        for table in tables:
            # Dynamically identify columns
            th_elements = table.find_all("th")
            headers = [th.text.strip().lower() for th in th_elements]
            
            if "name" not in headers:
                continue
                
            name_idx = headers.index("name")
            preview_idx = -1
            if "preview" in headers:
                preview_idx = headers.index("preview")
            elif "image" in headers:
                preview_idx = headers.index("image")
                
            if preview_idx == -1:
                continue

            rows = table.find_all("tr")
            for row in rows[1:]: # Skip assumed header row
                if count >= test_limit:
                    break
                    
                cells = row.find_all(["td", "th"])
                if len(cells) <= max(name_idx, preview_idx):
                    continue
                    
                # Extract image URL
                img_tag = cells[preview_idx].find("img")
                if not img_tag:
                    continue
                img_url = img_tag.get("src")
                if not img_url:
                    continue
                if img_url.startswith("/"):
                    img_url = "https://pso2na.arks-visiphone.com" + img_url
                    
                # Identify fake UI icons (skips menu icons from parsing)
                invalid_icons = ["NGSUI", "Menu", "Icon_", ".svg", "Ticket", "_logo"]
                if any(icon.lower() in img_url.lower() for icon in invalid_icons):
                    continue
                    
                # Extract Item Name
                item_name = cells[name_idx].text.strip().split("\n")[0].strip()
                if not item_name or "Unknown" in item_name or len(item_name) < 3:
                    continue

                print(f"  -> Found item: {item_name} | URL: {img_url}")
                
                # Tag it
                tags = self.auto_tag_item(item_name, img_url)
                
                # Upload it
                if "error" not in tags:
                    self.embed_and_upload(item_name, img_url, tags)
                
                results.append({
                    "name": item_name,
                    "thumbnail": img_url,
                    "tags": tags
                })
                count += 1
                
            if count >= test_limit:
                break
                
        return results

# --- Quick Test ---
if __name__ == "__main__":
    print("=== Testing Phashion Scraper ===")
    scraper = PhashionScraper()
    
    # 1. Get all categories dynamically instead of hardcoding
    category_urls = scraper.get_fashion_category_urls()
    
    # 2. Test scraping on the first 2 categories found
    all_results = []
    for cat_url in category_urls[:2]:
        cat_results = scraper.scrape_category(cat_url, test_limit=2)
        all_results.extend(cat_results)
        
    print("\n=== Fetched Items Summary ===")
    for item in all_results:
        print(f"- {item['name']} | Tags: {item.get('tags', {}).get('structural_tags', [])}")
