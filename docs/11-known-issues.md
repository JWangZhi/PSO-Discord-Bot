# Research: Bot Quality Issues + MCP Problems

Last updated: 2026-03-29

---

## Current Architecture (Post-Refactor)

```text
User Query
    │
    ├── RouterAgent (Gemini Flash) → intent: chat | wiki_search | fashion_match
    │
    ├── [wiki_search] → MCPBridge
    │   ├── Gemini resolves query → wiki page slug
    │   ├── Chrome DevTools navigates to arks-visiphone page
    │   ├── JS extracts page text (max 8000 chars)
    │   └── ChatAgent (Groq) synthesizes answer from page text
    │
    ├── [chat] → ChatAgent (Groq) with memory only
    │
    └── [fashion_match] → VisionAgent → ChatAgent (no fashion DB)
```

---

## Problem 1: MCP Cannot Access Arks-Visiphone

### Root Cause

The arks-visiphone wiki uses **CloudFlare protection** and/or **server-side caching** that blocks or delays headless Chrome connections. Common symptoms:
- HTTP 500 on first load (the code already retries once)
- CloudFlare challenge page served instead of wiki content
- Empty or truncated page content

### Why Chrome DevTools MCP Is Fragile for This Use Case

| Factor | Impact |
| --- | --- |
| CloudFlare bot detection | Headless Chrome gets challenged/blocked |
| MediaWiki rendering delay | JS `evaluate` may run before page fully renders |
| Single-page dependency | If one fetch fails, entire query fails with no fallback |
| Chrome process overhead | Spawning Chrome per bot instance is resource-heavy |

### Recommended Fix

The wiki already has a **public MediaWiki API** (`action=parse`) that returns clean HTML without CloudFlare interference. We already used this successfully in `wiki_scraper.py`. The MCP slug resolver approach is correct in concept, but the fetching layer should use the **API directly** instead of Chrome:

```python
# Instead of Chrome navigation:
# await self._session.call_tool("navigate", {"url": url})

# Use the MediaWiki API (no Chrome needed):
import requests
resp = requests.get(
    "https://pso2na.arks-visiphone.com/api.php",
    params={"action": "parse", "page": slug, "format": "json", "prop": "text"},
)
html = resp.json()["parse"]["text"]["*"]
```

This approach:
- Bypasses CloudFlare entirely (API endpoint is not protected the same way)
- Returns structured HTML (easier to parse than raw JS innerText)
- Is deterministic (no race conditions with page rendering)
- Eliminates Chrome dependency

---

## Problem 2: Bot Gives Wrong/Dumb Answers (The "Ngu" Problem)

### Root Cause Analysis

The Gemini AI Pro response nails the issue perfectly. The current bot is missing the **Conflict Detection** and **Semantic Bridging** steps. Here is what happens vs. what should happen:

### Current Flow (Broken)

```text
User: "What skills does Slayer have in PSO2 base?"
    │
    Router: intent=wiki_search ✓
    │
    MCP: resolve slug → "Slayer" or "Portal:New_Genesis/Slayer"
         (no validation that Slayer doesn't exist in base PSO2)
    │
    Fetch page → gets NGS Slayer page → feeds to LLM
    │
    LLM: answers with NGS Slayer skills as if they exist in base PSO2 ✗
```

### What Should Happen (Gemini's Pipeline)

```text
User: "What skills does Slayer have in PSO2 base?"
    │
    Step 1: Entity Extraction
    ├── entity: "Slayer" (class)
    ├── game_version: "pso2" (base)
    └── intent: "list skills"
    │
    Step 2: Conflict Detection ← MISSING FROM CURRENT BOT
    ├── Check: Does Slayer exist in PSO2 base? → NO
    ├── Slayer was released in 2023 for NGS only
    └── Flag: CONTRADICTION detected
    │
    Step 3: Semantic Bridging ← MISSING FROM CURRENT BOT
    ├── What is Slayer's defining trait? → Gunblade
    ├── What base PSO2 class uses Gunblade? → Luster
    └── Bridge: Slayer(NGS) ↔ Luster(PSO2 base)
    │
    Step 4: Response
    ├── Correct the premise: "Slayer doesn't exist in base PSO2"
    ├── Offer the bridge: "But Luster is the base PSO2 Gunblade class"
    └── Provide Luster skills from wiki data
```

### Why the Bot Lacks This

1. **No entity validation layer** — The bot trusts whatever the user says without cross-checking. If they say "Slayer in PSO2 base", the bot just searches for it.
2. **No game-entity mapping** — There's no structured knowledge of which entities belong to which game version.
3. **System prompt is too weak** — Saying "Do NOT mix PSO2 Classic and NGS" is not enough. The LLM needs **explicit conflict detection instructions** and a **reference list** of what exists where.

---

## Problem 3: Missing Cognitive Pipeline

The bot currently has a flat pipeline: `classify intent → fetch data → generate reply`. It's missing the intermediate intelligence layers that Gemini described. Here's the proposed 4-step cognitive pipeline:

### Step 1: Entity & Intent Extraction

Extract structured entities BEFORE searching:

```python
class QueryAnalysis:
    entities: list[str]       # ["Slayer", "Gunblade"]
    game_version: str         # "ngs" or "pso2"
    intent: str               # "list_skills", "compare", "explain"
    is_contradiction: bool    # True if entity doesn't exist in game_version
    suggested_bridge: str     # "Luster" if contradicted
```

### Step 2: Conflict Detection

Check extracted entities against a known entity-game mapping:

```python
ENTITY_GAME_MAP = {
    "ngs": {"Hunter", "Fighter", "Ranger", ..., "Slayer", "Waker"},
    "pso2": {"Hunter", "Fighter", "Ranger", ..., "Luster", "Phantom", "Etoile", "Hero"},
}

# Cross-reference table for bridging
ENTITY_BRIDGES = {
    ("Slayer", "pso2"): ("Luster", "ngs", "Both use Gunblade as primary weapon"),
    ("Luster", "ngs"): ("Slayer", "pso2", "Both use Gunblade as primary weapon"),
}
```

### Step 3: Semantic Bridging

If a contradiction is detected, the system should:
1. Correct the user's premise
2. Offer the closest equivalent
3. Fetch data for the corrected entity

### Step 4: Response Formulation

The LLM receives:
- The correction context (if any)
- The actual wiki data for the correct entity
- Explicit instructions to address the contradiction

---

## Problem 4: Groq LLM Limitations

The ChatAgent uses **Groq** (likely llama/mixtral) which has significantly less game-specific knowledge than Gemini Pro. This means:
- When wiki data is insufficient, Groq hallucinates more aggressively
- Groq doesn't naturally perform the "semantic bridging" that Gemini described
- The `[INSUFFICIENT_EVIDENCE]` guard works but is too aggressive — it returns a generic "I don't know" instead of trying to help

---

## Proposed Action Plan

| Priority | Action | Solves |
| --- | --- | --- |
| P0 | Replace Chrome fetch with MediaWiki API in MCPBridge | MCP access failure |
| P1 | Add entity-game mapping for conflict detection | Wrong answers for cross-game queries |
| P1 | Add conflict detection to slug resolver (before fetch) | Slayer-in-base problem |
| P2 | Implement semantic bridging with cross-reference table | "Ngu" answers |
| P2 | Upgrade system prompt with explicit reasoning instructions | Answer quality |
| P3 | Consider Gemini as ChatAgent backend (not just router) | Overall intelligence |

### P0: MediaWiki API Fetch (Fastest Fix)

Replace the Chrome DevTools fetch in `mcp_client.py` with a direct API call. This:
- Fixes the CloudFlare blocking issue
- Removes Chrome dependency
- Is faster and more reliable
- Can be done without changing any other code (same interface)

### P1: Entity Validation Layer

Add a lightweight validation step between the router and the fetcher:
1. Extract entities from the query (Gemini or regex)
2. Check if entities exist in the specified game version
3. If contradiction → bridge to correct entity before fetching

### P2: Enhanced System Prompt

Instead of a simple "don't mix games" instruction, give the LLM explicit reasoning steps:
```
Before answering, verify:
1. Does the entity exist in the specified game version?
2. If not, what is the closest equivalent?
3. State the correction before providing data.
```

---

## Summary

The bot's core problems are:
1. **MCP/Chrome can't reliably access the wiki** → Use MediaWiki API instead
2. **No conflict detection** → Bot blindly answers impossible queries
3. **No semantic bridging** → Bot doesn't "think" about cross-game equivalents
4. **Groq is not smart enough** for complex reasoning → Consider Gemini for synthesis
