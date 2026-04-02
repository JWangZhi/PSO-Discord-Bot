"""
Vision Agent

Analyzes outfit images sent by players using Google Gemini Vision API.
It specifically ignores mutable traits (colors, face, lighting) and focuses
on structural features to generate a list of searchable tags.
"""

import json

from google import genai
from google.genai import types
from settings import env as config


class VisionAgent:
    """Agent responsible for Reverse Image Look-up for Phashion.

    Uses Google Gemini Vision API (free tier).
    """

    def __init__(self):
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        self._model = "gemini-2.5-flash"

    @staticmethod
    def _create_prompt() -> str:
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

    async def analyze_outfit_from_bytes(self, image_data: bytes, mime_type: str = "image/jpeg") -> dict:
        """Analyze outfit image using Gemini Vision API."""
        try:
            prompt = self._create_prompt()
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=[
                    prompt,
                    types.Part.from_bytes(data=image_data, mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                    max_output_tokens=512,
                ),
            )
            text_response = response.text.strip()
            return json.loads(text_response)
        except json.JSONDecodeError as e:
            print(f"[VisionAgent] Failed to parse JSON: {e}")
            return {"error": "Invalid JSON response from AI"}
        except Exception as e:
            print(f"[VisionAgent] Error analyzing image: {e}")
            return {"error": str(e)}
