"""
Vision Agent

Analyzes outfit images sent by players using a local Vision-Language Model (VLM).
It specifically ignores mutable traits (colors, face, lighting) and focuses
on structural features to generate a list of searchable tags.

Supports two backends:
- Local VLM via LM Studio (OpenAI-compatible API) [ACTIVE]
- Google Gemini Cloud API [COMMENTED OUT - for future use / higher rate limits]
"""

import sys
import json
import base64
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
import config

# --- [COMMENTED OUT] Gemini Cloud Backend ---
# from google import genai
# class GeminiVisionAgent:
#     """Vision Agent using Google Gemini Cloud API."""
#     def __init__(self):
#         self.client = genai.Client(api_key=config.GEMINI_API_KEY)
#         self.model_name = 'gemini-2.5-flash'
#
#     def analyze_outfit_from_bytes(self, image_data: bytes, mime_type: str = "image/jpeg") -> dict:
#         try:
#             prompt = VisionAgent._create_prompt()
#             response = self.client.models.generate_content(
#                 model=self.model_name,
#                 contents=[
#                     prompt,
#                     genai.types.Part.from_bytes(data=image_data, mime_type=mime_type)
#                 ]
#             )
#             text_response = response.text.strip()
#             if text_response.startswith('```json'):
#                 text_response = text_response[7:]
#             if text_response.startswith('```'):
#                 text_response = text_response[3:]
#             if text_response.endswith('```'):
#                 text_response = text_response[:-3]
#             return json.loads(text_response.strip())
#         except Exception as e:
#             print(f"[VisionAgent/Gemini] Error: {e}")
#             return {"error": str(e)}
# --- [END COMMENTED OUT] ---


class VisionAgent:
    """Agent responsible for Reverse Image Look-up for Phashion.
    
    Uses a local Vision-Language Model (VLM) served by LM Studio.
    """

    # LM Studio local server endpoint
    LOCAL_VLM_URL = "http://127.0.0.1:9707/v1"

    def __init__(self):
        self.client = OpenAI(
            base_url=self.LOCAL_VLM_URL,
            api_key="lm-studio",  # LM Studio doesn't require a real key
        )

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

    def analyze_outfit_from_bytes(self, image_data: bytes, mime_type: str = "image/jpeg") -> dict:
        """
        Analyze an outfit from image bytes using the local VLM.
        """
        try:
            prompt = self._create_prompt()

            # Encode image to base64 for OpenAI vision API format
            b64_image = base64.b64encode(image_data).decode("utf-8")

            response = self.client.chat.completions.create(
                model="local-model",  # LM Studio uses whatever model is loaded
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{b64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=512,
                temperature=0.2,
            )

            text_response = response.choices[0].message.content.strip()

            # Clean possible markdown block indicators
            if text_response.startswith('```json'):
                text_response = text_response[7:]
            if text_response.startswith('```'):
                text_response = text_response[3:]
            if text_response.endswith('```'):
                text_response = text_response[:-3]

            result = json.loads(text_response.strip())
            return result

        except json.JSONDecodeError as e:
            print(f"[VisionAgent] Failed to parse JSON from local VLM: {e}")
            print(f"Raw response: {text_response}")
            return {"error": "Invalid JSON response from AI"}
        except Exception as e: # pylint: disable=broad-exception-caught
            print(f"[VisionAgent] Error analyzing image: {e}")
            return {"error": str(e)}


# --- Quick Test ---
if __name__ == "__main__":
    import requests

    agent = VisionAgent()

    print("Downloading sample image...")
    img_url = "https://cataas.com/cat"
    resp = requests.get(img_url, timeout=10)

    if resp.status_code == 200:
        print("Analyzing image with Local VLM...")
        result = agent.analyze_outfit_from_bytes(resp.content)
        print("\n=== Extraction Result ===")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Failed to download sample image.")
