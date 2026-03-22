"""
Phashion Matcher

This module receives the tags extracted by the VisionAgent from a user's uploaded image,
converts these tags into a Vector using the Local Embedding Model, and searches
the Pinecone Database (pso2-phashion index) for the closest match.
"""

import sys
from pathlib import Path
from dataclasses import dataclass

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from pinecone import Pinecone

import config

@dataclass
class PhashionMatch:
    """A matched Phashion item from the Database."""
    item_name: str
    thumbnail_url: str
    similarity_score: float

class PhashionMatcher:
    def __init__(self):
        # Local Embedding client (LM Studio)
        self._embed_client = OpenAI(
            base_url=config.LOCAL_EMBED_URL,
            api_key="lm-studio",
        )

        # Pinecone client
        pc = Pinecone(api_key=config.PINECONE_API_KEY)
        # Using a dedicated index for Phashion to keep vectors separate from text Wiki
        self._index_name = "pso2-phashion"
        
        # Check if index exists, but don't crash if it doesn't (user might not have run scraper yet)
        if self._index_name in [idx.name for idx in pc.list_indexes()]:
            self._index = pc.Index(self._index_name)
        else:
            self._index = None
            print(f"[PhashionMatcher] Warning: Index '{self._index_name}' does not exist yet.")

    def embed_tags(self, tags_dict: dict) -> list[float] | None:
        """
        Convert the VisionAgent JSON output into a single searchable string, then embed it.
        """
        if "error" in tags_dict:
            return None
            
        # Compile tags into a descriptive string
        gender = tags_dict.get("gender_focus", "")
        theme = tags_dict.get("style_theme", "")
        structural = ", ".join(tags_dict.get("structural_tags", []))
        
        search_query = f"{gender} {theme}. Features: {structural}"
        print(f"[PhashionMatcher] Embedding query: {search_query}")
        
        try:
            response = self._embed_client.embeddings.create(
                model=config.LOCAL_EMBED_MODEL,
                input=[search_query],
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"[PhashionMatcher] Error embedding tags: {e}")
            return None

    def find_matches(self, tags_dict: dict, top_k: int = 3) -> list[PhashionMatch]:
        """
        Search Pinecone for the closest fashion items based on the provided tags.
        """
        if not self._index:
            print("[PhashionMatcher] Error: Database index not available.")
            return []
            
        vector = self.embed_tags(tags_dict)
        if not vector:
            return []

        try:
            results = self._index.query(
                vector=vector,
                top_k=top_k,
                include_metadata=True,
            )

            matches = []
            for match in results.matches:
                meta = match.metadata or {}
                matches.append(PhashionMatch(
                    item_name=meta.get("name", "Unknown Item"),
                    thumbnail_url=meta.get("thumbnail", ""),
                    similarity_score=match.score
                ))
            return matches
            
        except Exception as e:
            print(f"[PhashionMatcher] Error querying Pinecone: {e}")
            return []

# --- Quick Test ---
if __name__ == "__main__":
    print("=== Test Phashion Matcher ===\n")
    matcher = PhashionMatcher()
    
    # Mock tags from the Vision Agent
    mock_tags = {
      "gender_focus": "Female/T2",
      "style_theme": "Sci-fi uniform",
      "structural_tags": [
        "Full-body jumpsuit silhouette",
        "Form-fitting silhouette",
        "One-piece garment appearance"
      ]
    }
    
    # We can't actually search if the DB isn't populated, but we can test embedding
    vec = matcher.embed_tags(mock_tags)
    if vec:
        print(f"Successfully embedded tags into vector of length {len(vec)}")
    else:
        print("Failed to embed tags.")
