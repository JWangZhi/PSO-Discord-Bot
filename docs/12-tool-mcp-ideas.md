# Tool Calling & MCP Integration — Ideas for PSO2 Bot

Last updated: 2026-03-28

---

## Overview

Instead of relying solely on RAG (stuffing context into prompts), the bot can use **Tool Calling** (function calling) and/or **MCP (Model Context Protocol)** to let the LLM actively fetch structured data at query time.

This solves ISS-001/002/003 fundamentally: the LLM won't hallucinate when it can call a tool to get exact data.

---

## Approach 1: Native Tool Calling (Function Calling)

The LLM (Groq/Gemini) receives a list of available functions. When a user asks a question, the LLM decides which function(s) to call, receives results, then synthesizes the answer.

### Proposed Tools

| Tool | Description | Data Source |
| --- | --- | --- |
| `search_wiki_text` | Text search across wiki text chunks | MongoDB (`wiki_chunks`) |
| `lookup_class_skills` | Get all skills for a specific class | MongoDB (`wiki_tables`) |
| `lookup_weapon_stats` | Get stats for a specific weapon or series | MongoDB (`wiki_tables`) |
| `lookup_item` | Search for any item by name | MongoDB (`wiki_tables`) |
| `compare_weapons` | Compare stats between 2+ weapons | MongoDB (`wiki_tables`) |
| `search_official_site` | Search the official PSO2:NGS Players site | `pso2.com/players/` scraper |
| `list_classes` | List all classes for a game version | Manifest / hardcoded |

### Example Flow

```
User: "What skills does Ranger have in NGS?"

LLM thinks: I need skill data → call lookup_class_skills(class="Ranger", game="ngs")

Tool returns: [{name: "Blight Rounds", type: "Active", desc: "..."}, ...]

LLM synthesizes: "Ranger in NGS has the following skills: ..."
```

### Implementation Sketch

```python
# Define tools as JSON schema for Groq/Gemini
tools = [
    {
        "type": "function",
        "function": {
            "name": "lookup_class_skills",
            "description": "Get all skills for a specific class in PSO2 or NGS",
            "parameters": {
                "type": "object",
                "properties": {
                    "class_name": {"type": "string", "description": "e.g. Hunter, Ranger, Force"},
                    "game_mode": {"type": "string", "enum": ["ngs", "pso2"]},
                },
                "required": ["class_name", "game_mode"],
            },
        },
    },
    # ... more tools
]

# In ChatAgent.generate_reply():
response = await client.chat.completions.create(
    model=model,
    messages=messages,
    tools=tools,
    tool_choice="auto",
)

# If LLM wants to call a tool:
if response.choices[0].message.tool_calls:
    for call in response.choices[0].message.tool_calls:
        result = execute_tool(call.function.name, call.function.arguments)
        messages.append({"role": "tool", "content": result, "tool_call_id": call.id})
    # Re-call LLM with tool results
    final = await client.chat.completions.create(model=model, messages=messages)
```

### Pros / Cons

| Pros | Cons |
| --- | --- |
| LLM decides what data it needs | Extra API call (tool call + follow-up) |
| No hallucination on structured data | Groq free tier may have tool-calling limits |
| Clean separation of concerns | Need to define + maintain tool schemas |
| Works with any LLM that supports function calling | Latency increases (~2x for tool round-trip) |

---

## Approach 2: MCP Server

Run a local MCP server that exposes PSO2 data as **resources** and **tools**. Any MCP-compatible client (Claude Desktop, Cursor, custom bot) can connect to it.

### Architecture

```
Discord Bot (MCP Client)
    │
    ├── MCP Server (localhost:8080)
    │   ├── Resources:
    │   │   ├── pso2://classes/{game}/{class_name}
    │   │   ├── pso2://weapons/{game}/{weapon_type}
    │   │   ├── pso2://skills/{game}/{class_name}
    │   │   ├── pso2://items/{item_name}
    │   │   └── pso2://official/{article_id}
    │   │
    │   └── Tools:
    │       ├── search_wiki(query, game_mode) → text chunks
    │       ├── lookup_stats(item_name, game_mode) → structured data
    │       ├── search_official_site(query) → official articles
    │       └── compare(item_a, item_b) → comparison table
    │
    └── LLM (Groq/Gemini) ← receives MCP tool results as context
```

### MCP Server Implementation

```python
# mcp_server.py — using the `mcp` Python SDK
from mcp.server import Server
from mcp.types import Resource, Tool

server = Server("pso2-knowledge")

@server.resource("pso2://classes/{game}/{class_name}")
async def get_class(game: str, class_name: str):
    """Return class overview + skills from MongoDB."""
    # Query wiki_tables for class data
    ...

@server.tool("search_wiki")
async def search_wiki(query: str, game_mode: str = "ngs"):
    """Semantic search across wiki chunks."""
    # Query MongoDB text search
    ...

@server.tool("lookup_stats")
async def lookup_stats(item_name: str, game_mode: str = "ngs"):
    """Exact match lookup in MongoDB."""
    # Query wiki_tables
    ...
```

### Pros / Cons

| Pros | Cons |
| --- | --- |
| Standardized protocol (works with any MCP client) | Extra infra to maintain (MCP server process) |
| Reusable beyond Discord (Cursor, Claude Desktop, etc.) | MCP SDK is still evolving |
| Resources are browseable (not just tools) | Overkill if only used by one bot |
| Composable with other MCP servers | Need to handle auth/security |

---

## Recommendation

| Criteria | Tool Calling | MCP Server |
| --- | --- | --- |
| Complexity | LOW | MEDIUM |
| Time to implement | 1-2 days | 3-5 days |
| Reusability | Bot only | Any MCP client |
| Best for | Single Discord bot | Multi-client ecosystem |

### Short-term: Start with **Tool Calling**

- Fastest path to fixing ISS-001/002/003.
- Groq and Gemini both support function calling natively.
- Define 3-4 core tools, wire them into `ChatAgent`.

### Long-term: Migrate to **MCP Server**

- When the data layer is stable and tested via tool calling.
- Wrap the same tool functions in an MCP server.
- Benefit: use the same PSO2 knowledge in Cursor/Claude for development assistance.

---

## Query Interpreter Layer (Pre-Tool Intelligence)

Before calling any tool, the bot needs a **Query Interpreter** that converts vague Discord messages into precise tool calls. This is the "brain" that sits between the user and the tools.

### Core Behavior

1. **Interpret vague queries** — Users rarely ask precise questions. The interpreter infers 1-3 likely interpretations from keywords and conversational context.
2. **Search before answering** — Never guess. Find the correct page/source first, then extract relevant data.
3. **Clarify only when necessary** — If multiple near-equal candidates exist, ask ONE short question. Otherwise, pick the best match and proceed.

### Resolution Priority

When matching a user query to data:

1. Page title (exact or fuzzy match)
2. URL / canonical page identity
3. Section headings within a page
4. Structured fields (class name, weapon name, skill name)
5. Semantic text search (last resort)

### Search Strategy

```
User query
    │
    ├── Extract entities: class name? weapon? skill? game version?
    │
    ├── Broad search with context (game + category)
    │   └── If ambiguous → narrow by: page type, feature name, category
    │
    ├── Prefer canonical pages over subpages or fragments
    │
    └── Structured extraction > flattening entire page into chunks
```

### Tool Selection Logic

```
User: "What skills does Ranger have?"
    │
    Interpreter detects: class="Ranger", data_type="skills", game="ngs" (from context)
    │
    ├── Primary:   lookup_class_skills(class="Ranger", game="ngs")
    ├── Fallback:  search_wiki_text("Ranger skills", game="ngs")
    └── Output:    Structured skill list from MongoDB

User: "Is Rivalate good?"
    │
    Interpreter detects: weapon_series="Rivalate", intent="evaluation"
    │
    ├── Step 1:    lookup_weapon_stats(name="Rivalate", game="pso2")
    ├── Step 2:    search_wiki_text("Rivalate series review", game="pso2")
    └── Output:    Stats + contextual text merged for LLM synthesis

User: "Find me info about that wiki page"
    │
    Interpreter detects: vague reference, needs prior context
    │
    ├── Check conversation history for last mentioned topic
    ├── search_wiki_text(inferred_topic)
    └── If still ambiguous → ask: "Which page? The Ranger class page or the Rifles weapon page?"
```

### Clarification Rules

| Condition | Action |
| --- | --- |
| Clear intent, single match | Answer immediately |
| Clear intent, 2-3 matches | Pick best match, note alternatives |
| Vague intent, multiple equal candidates | Ask ONE short clarifying question |
| Too broad to resolve safely | Ask: "Do you mean X or Y?" |

### Response Style

- Concise and direct.
- Do not explain internal reasoning to the user.
- Do not mention tools, databases, or pipeline internals.
- If uncertain, say so plainly: "I found partial info, but the wiki doesn't cover X in detail."
- Give the most useful answer first, source link second.

### System Prompt Integration

This behavior is encoded in the system prompt for the ChatAgent when tool calling is enabled:

```python
TOOL_SYSTEM_PROMPT = """
You are an ARKS System Advisor for Phantasy Star Online 2.
You have access to search tools to find accurate game data.

Rules:
- ALWAYS use a tool to look up data before answering factual questions.
- Do NOT guess stats, skill names, or mechanics from memory.
- If the user's question is vague, infer the most likely intent and search.
- If multiple interpretations are equally valid, ask ONE short clarifying question.
- Keep answers concise. Use bullet points for lists.
- Do NOT mix PSO2 Classic and NGS mechanics.
- When uncertain, say: "Data not found in the ARKS database."
"""
```

---

## Priority Tools to Implement First

1. **`lookup_class_skills`** — Solves ISS-003 directly. The interpreter routes "what skills does X have?" here instead of RAG.
2. **`search_wiki_text`** — Replaces blind RAG stuffing. The interpreter adds `game_mode` filter automatically.
3. **`lookup_weapon_stats`** — Enables precise stat lookups. The interpreter detects weapon/series names from the query.
