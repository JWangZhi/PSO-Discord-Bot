"""
Vision Agent

Uses Google Gemini 1.5 Flash to analyze outfit images sent by players.
It specifically ignores mutable traits (colors, face, lighting) and focuses 
on structural features to generate a list of searchable tags.
"""

import os
import sys
import json
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from google import genai
import config

class VisionAgent:
    """Agent responsible for Reverse Image Look-up for Phashion."""

    def __init__(self):
        # Configure Gemini API using the new Client
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.model_name = 'gemini-2.5-flash'
        
    def _create_prompt(self) -> str:
        """Create the strict prompt for the Vision AI."""
        return """
        You are an elite Fashion Analyst (Phashion) for the game Phantasy Star Online 2.
        Your task is to analyze the provided screenshot/image of an in-game character 
        and extract identifying structural tags so we can find the outfit in a database.

        CRITICAL RULES for Phantasy Star Online 2:
        1. In this game, players can customize colors, faces, body proportions, and skin tones.
        2. DO NOT use colors as identifying tags.
        3. DO NOT describe the face, hair color, or lighting.
        4. YOU MUST FOCUS EXCLUSIVELY on STRUCTURAL features (silhouettes, materials, specific shapes).

        Look for and extract tags regarding:
        - Silhouette & Style (e.g., Asymmetrical coat, Pleated Skirt, Mecha Suit, Tactical vest).
        - Geometric details (e.g., Twin shoulder belts, Heavy mechanical calf thrusters, Spiked collar).
        - Material textures (e.g., Glossy leather, Heavy metal plates, Ruffled lace, Holographic decals).
        - Distinct accessories (e.g., Cybernetic angel wings, Gas mask, Robotic tail).

        OUTPUT FORMAT:
        You must return ONLY a raw JSON object with the following structure. Do not include markdown formatting or extra text.
        {
            "gender_focus": "Male/T1 or Female/T2 or Cast",
            "style_theme": "e.g., Sci-fi military, Casual streetwear, Fantasy formal",
            "structural_tags": ["tag1", "tag2", "tag3", "tag4", "tag5"]
        }
        """

    def analyze_outfit_from_bytes(self, image_data: bytes, mime_type: str = "image/jpeg") -> dict:
        """
        Analyze an outfit from image bytes.
        """
        try:
            prompt = self._create_prompt()
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    prompt,
                    genai.types.Part.from_bytes(data=image_data, mime_type=mime_type)
                ]
            )
            text_response = response.text.strip()
            
            # Clean possible markdown block indicators if the LLM disobeys the prompt slightly
            if text_response.startswith('```json'):
                text_response = text_response[7:]
            if text_response.startswith('```'):
                text_response = text_response[3:]
            if text_response.endswith('```'):
                text_response = text_response[:-3]
                
            result = json.loads(text_response.strip())
            return result
            
        except json.JSONDecodeError as e:
            print(f"[VisionAgent] Failed to parse JSON from Gemini: {e}")
            print(f"Raw response: {response.text}")
            return {"error": "Invalid JSON response from AI"}
        except Exception as e:
            print(f"[VisionAgent] Error analyzing image: {e}")
            return {"error": str(e)}

# --- Quick Test ---
if __name__ == "__main__":
    import requests
    
    agent = VisionAgent()
    
    # Example placeholder image from internet just to test API connection
    print("Downloading sample image...")
    img_url = "https://cataas.com/cat" # Using a cat picture just to test API connection and JSON formatting
    response = requests.get(img_url)
    
    if response.status_code == 200:
        print("Analyzing image with Gemini Vision...")
        result = agent.analyze_outfit_from_bytes(response.content)
        print("\n=== Extraction Result ===")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Failed to download sample image.")
