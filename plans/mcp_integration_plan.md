# MCP Integration Plan — Live Web Fetching for PSO2 Bot

## Goal
Give the LLM the ability to fetch live web content (PSO2/NGS wiki pages, patch notes, game updates) during conversations, replacing the removed static Pinecone vector DB.

---

## Architecture Overview

```
Discord User
    │
    ▼
PSO2Bot (Discord Client)
    │
    ▼
ChatAgent (MCP Host)
    │  ← acts as MCP Host: discovers tools, routes tool calls
    ▼
MCP ClientSession ──stdio──▶ MCP Server (local subprocess)
                                │
                                ├─ fetch_webpage tool (generic URL fetch → markdown)
                                ├─ search_wiki tool (search pso2.arks-visiphone.com)
                                └─ get_patch_notes tool (fetch latest update info)
```

**Key insight**: Groq API does NOT natively support MCP. The bot must implement the MCP Host logic itself:
1. ChatAgent decides it needs web data (via prompt engineering or a simple keyword heuristic)
2. Bot calls MCP server tools via `ClientSession`
3. Results are injected as `extra_context` into the LLM system prompt (existing pattern)

---

## Components

### 1. MCP Server (`core/mcp/wiki_server.py`)

A FastMCP server exposing PSO2-specific web tools:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pso2-wiki")

@mcp.tool()
async def fetch_webpage(url: str, max_length: int = 5000) -> str:
    """Fetch a URL and return content as markdown."""
    # Uses httpx + readability/html2text
    ...

@mcp.tool()
async def search_wiki(query: str, game_version: str = "NGS") -> str:
    """Search the PSO2/NGS wiki for a topic."""
    # Constructs wiki search URL, fetches results
    ...

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**Alternative (Phase 1)**: Use the official `mcp-server-fetch` directly (`uvx mcp-server-fetch`) which already provides a `fetch` tool. This requires zero custom server code.

### 2. MCP Client in ChatAgent (`core/mcp/mcp_client.py`)

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPBridge:
    """Manages connection to MCP server and tool execution."""
    
    async def start(self):
        """Initialize MCP client session."""
        server_params = StdioServerParameters(
            command="uvx",
            args=["mcp-server-fetch"],  # or "python", "core/mcp/wiki_server.py"
        )
        # Store transport for lifecycle management
        ...

    async def fetch(self, url: str, max_length: int = 5000) -> str:
        """Call the fetch tool on the MCP server."""
        result = await self.session.call_tool("fetch", {"url": url, "max_length": max_length})
        return result.content[0].text

    async def close(self):
        """Clean shutdown."""
        ...
```

### 3. Integration Point: ChatAgent

The ChatAgent gets an `MCPBridge` instance. When `extra_context` contains a URL or the router identifies a "needs web data" intent:

```python
# In ChatAgent.generate_reply():
if needs_web_fetch:
    web_content = await self.mcp.fetch(url)
    extra_context += f"\n--- Live Web Data ---\n{web_content}\n"
```

---

## Phased Implementation

### Phase 1 — Official Fetch Server (Simplest)
- Install: `uv add "mcp[cli]"` + use `mcp-server-fetch` via uvx
- Create `MCPBridge` class that connects to `mcp-server-fetch` via stdio
- Add a `/wiki` slash command that takes a URL, fetches via MCP, and returns content
- No changes to ChatAgent's core flow yet

### Phase 2 — ChatAgent Integration
- ChatAgent gets `MCPBridge` injected
- RouterAgent gets a new intent: `"web_lookup"` 
- When router classifies as web_lookup, ChatAgent uses MCP to fetch relevant wiki pages
- Fetched content injected as `extra_context` (existing pattern)

### Phase 3 — Custom MCP Server
- Build `core/mcp/wiki_server.py` with PSO2-specific tools:
  - `search_wiki(query)` — knows wiki URL structure, searches efficiently
  - `get_class_info(class_name)` — fetches specific class page
  - `get_patch_notes()` — fetches latest update notes
- Replace `mcp-server-fetch` with custom server in MCPBridge config

### Phase 4 — LLM Tool Calling (Optional)
- If/when Groq supports tool calling with Llama models:
  - Expose MCP tools as Groq function definitions
  - Let the LLM decide when to call tools autonomously
  - Full agentic loop: LLM → tool call → result → LLM → response

---

## Dependencies

```toml
# pyproject.toml additions
[project.dependencies]
mcp = { version = ">=1.0", extras = ["cli"] }
```

---

## Transport Choice

**STDIO** (recommended for Phase 1-3):
- Simplest setup — MCP server runs as a subprocess
- No networking, no auth needed
- Perfect for single-bot deployment

**Streamable HTTP** (future, Phase 4+):
- If MCP server needs to be shared across multiple bot instances
- Or if deploying MCP server separately (e.g., in Docker)

---

## File Structure

```
core/
  mcp/
    __init__.py
    mcp_client.py      # MCPBridge class
    wiki_server.py      # Custom FastMCP server (Phase 3)
```

---

## Security Notes

- MCP fetch server can access local/internal IPs — restrict via proxy config or URL allowlist
- Validate URLs before passing to fetch tool (only allow known wiki domains)
- Set `max_length` to prevent token overflow from large pages
- robots.txt respected by default in `mcp-server-fetch`

---

## Key URLs for PSO2/NGS Wiki

- Main wiki: `https://pso2.arks-visiphone.com/wiki/`
- NGS prefix: `https://pso2.arks-visiphone.com/wiki/Portal:New_Genesis/`
- Patch notes: `https://pso2.arks-visiphone.com/wiki/Portal:New_Genesis/News`
