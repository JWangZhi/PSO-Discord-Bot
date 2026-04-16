"""
Router Agent (Intent Matrix)

Serves as the front door for the bot. It analyzes the user's message and attachments
to determine the precise intent. This saves tokens by not triggering expensive
Memory compression or RAG searches for simple chat messages.

Intents:
1. 'chat': General roleplay, greetings, small talk. Route to normal Memory/Persona.
2. 'wiki_search': Questions about game mechanics, classes, lore. Route to RAG Pipeline.
3. 'fashion_match': User uploaded an image asking "what outfit is this?". Route to Phashion Matcher.
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import re
from google import genai
from google.genai import types
from pydantic import BaseModel
from settings import env as config
from settings import app as app_settings

class IntentResult(BaseModel):
    intent: str
    confidence: float
    reasoning: str
    game_version: str = "ngs"

class RouterAgent:
    def __init__(self):
        # We use a fast, cheap model for routing
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        self._model_name = app_settings.ROUTER_MODEL
        
        self._system_prompt = """
You are the master router for the PSO2 Discord bot. Your job is to classify the user's 
message into exactly one of three intents: 'chat', 'wiki_search', or 'fashion_match'.

Rules:
- 'fashion_match': The user has uploaded an image and wants to know what clothing/outfit/fashion it is. 
   Keywords: "what is this", "outfit", "look", "wear". (If `has_image` is true, heavily bias towards this).
- 'wiki_search': The user is asking a factual question about Phantasy Star Online 2 (NGS) gameplay, classes, skills, weapons, or story. 
   Keywords: "how to", "where to find", "what does", "guide", "wiki".
- 'chat': The user is just chatting, saying hello, or roleplaying with the bot character (Matoi, Xiera).

Additionally, determine the game version:
- 'ngs': Default. Questions about PSO2: New Genesis.
- 'pso2': Questions about PSO2 Classic / base game, or classes/entities that ONLY exist in PSO2 Classic.

ENTITY-GAME MAP (use this to classify):
- PSO2-ONLY classes (do NOT exist in NGS): Phantom, Hero, Etoile, Luster, Summoner.
- NGS-ONLY classes (do NOT exist in PSO2 Classic): Slayer, Waker.
- Both games: Hunter, Fighter, Ranger, Gunner, Force, Techter, Braver, Bouncer.
- If unsure, default to 'ngs'.

Output as JSON matching the schema.
"""

    def classify_intent(self, user_message: str, has_image: bool = False) -> IntentResult:
        """
        Determine the user's intent.
        """
        # Fast local short-circuit rules (Token Optimization)
        # If there is no text but there is an image, it's obviously a fashion search.
        if has_image and not user_message.strip():
            return IntentResult(
                intent="fashion_match", 
                confidence=1.0, 
                reasoning="Fast route: Image uploaded with no text."
            )
            
        # Fast route for simple greetings (exact match)
        lower_msg = user_message.strip().lower()
        if not has_image and lower_msg in app_settings.ROUTER_SIMPLE_CHAT_TERMS:
            return IntentResult(
                intent="chat", 
                confidence=1.0, 
                reasoning="Fast route: Simple greeting."
            )

        # Fast route for greeting-like patterns (regex)
        if not has_image and any(
            re.search(pat, lower_msg, re.IGNORECASE)
            for pat in app_settings.ROUTER_FAST_CHAT_PATTERNS
        ):
            return IntentResult(
                intent="chat",
                confidence=0.95,
                reasoning="Fast route: Greeting pattern match."
            )
            
        # If it bypassed fast routes, use LLM
        prompt_context = f"Message: '{user_message}'\nHas Image Attached: {has_image}"
        
        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=prompt_context,
                config=types.GenerateContentConfig(
                    system_instruction=self._system_prompt,
                    response_mime_type="application/json",
                    response_schema=IntentResult,
                    temperature=app_settings.ROUTER_TEMPERATURE # Low temp for deterministic classification
                ),
            )
            
            # The SDK parses it directly into the schema object if we requested it, 
            # but usually it returns text that we json.loads
            import json
            data = json.loads(response.text)
            return IntentResult(**data)
            
        except Exception as e:
            print(f"[RouterAgent] Routing failed: {e}")
            # Fallback intent
            return IntentResult(intent="chat", confidence=0.0, reasoning=f"Error: {str(e)}")


# --- Quick Test ---
if __name__ == "__main__":
    print("=== Test Router Agent ===")
    router = RouterAgent()
    
    test_cases = [
        {"msg": "Hi Matoi!", "img": False},
        {"msg": "How do I play the Braver class?", "img": False},
        {"msg": "Does anyone know what basewear she is wearing in this pic?", "img": True},
        {"msg": "", "img": True}
    ]
    
    for case in test_cases:
        res = router.classify_intent(case["msg"], case["img"])
        print(f"\nUser: '{case['msg']}' (Has Image: {case['img']})")
        if isinstance(res, IntentResult):
            print(f"-> Intent: {res.intent} (Confidence: {res.confidence})")
            print(f"-> Reason: {res.reasoning}")
        else:
            print(f"-> Result: {res}")
