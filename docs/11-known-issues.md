# Known Issues — Wiki Scraper & RAG Pipeline

Last updated: 2026-03-26

---

## CRITICAL — Directly impacts answer quality

### ISS-001: Text chunks contain navigation tables (garbage)

- **File:** `wiki_scraper.py` → `extract_text_chunks()` → `flush()`
- **Description:** Each class page on the Wiki has a navigation bar at the top (class selector table: Hunter/Fighter/Ranger/...). During chunking, this table gets converted to markdown and lands in the `[Introduction][Overview]` chunk, degrading embedding quality.
- **Example:** Ranger's Introduction chunk contains `| Hunter | Fighter | Ranger | Gunner |...` instead of actual class description.
- **Impact:** Pinecone returns Introduction chunks with low scores (~0.62), content is useless, LLM fills the gap with hallucinated training data.
- **Attempted fixes:**
  - Strip ALL `<table>` tags → Lost all skill data (skills are 100% table-based on Wiki).
  - Smart heuristic (strip if link_ratio > 0.5) → Reverted; needs more refinement.
- **Status:** OPEN — Need a better approach to distinguish nav tables vs content tables.

---

### ISS-002: LLM hallucination — mixing PSO2 Classic and NGS mechanics

- **File:** `main.py`, `core/agents/chat_agent.py`, `rag_pipeline.py`
- **Description:** When RAG context is insufficient or contains garbage, the LLM fills gaps with its training knowledge, which includes PSO2 Classic mechanics (e.g., claiming NGS Rangers can use Technics like Resta/Shifta — completely wrong).
- **Fixes applied:**
  - Added `game_mode` filter to Pinecone query ✅
  - Added strict instruction to system prompt ("Do NOT mix PSO2 Classic and NGS") ✅
- **Status:** PARTIALLY FIXED — Filter works correctly, but when chunk quality is low (ISS-001), LLM still hallucinates.

---

### ISS-003: Skill data not searchable via RAG

- **File:** `wiki_scraper.py`, `embed_uploader.py`
- **Description:** On the Wiki, the Skills section is nearly 100% `<table>` elements. If tables are stripped from chunks → all skill data is lost, Bot replies "Data Not Found". If tables are kept → chunks become oversized (23KB for Ranger Skills) and contain noisy markdown table syntax.
- **Note:** Skill data IS available in `.tables.json` and MongoDB (`wiki_tables`), but `rag_pipeline.py` only searches MongoDB via regex on a limited set of field names → cannot find skill descriptions.
- **Status:** OPEN — Need to improve how skill data is organized and retrieved.

---

## MEDIUM — Impacts user experience

### ISS-004: Discord 2000-character message limit

- **File:** `main.py`
- **Description:** Discord rejects messages over 2000 characters. When LLM generates long replies, Bot crashes with `HTTPException 400`.
- **Fix applied:** Added `send_long_message()` helper to auto-split messages ✅
- **Status:** FIXED

---

### ISS-005: Pinecone metadata 512-byte limit

- **File:** `embed_uploader.py`
- **Description:** Pinecone caps metadata values at 512 bytes. Long chunk content gets truncated in the `text` metadata field, potentially losing important context returned to the LLM.
- **Fix applied:** Added `truncate_metadata_value()` capping at 500 bytes ✅
- **Status:** FIXED — May need to evaluate if 500 bytes provides enough context.

---

## LOW — No immediate impact

### ISS-006: 25 Wiki pages do not exist (Redlinks)

- **File:** `wiki_scraper_manifest.json`
- **Description:** 25 pages in the manifest are missing from the Wiki (e.g., Crafting, Damage_Formula, FUN_Shop, AC_Scratches...). Scraper logs `[SKIP] Redlink (missing)`.
- **Status:** KNOWN — Need to audit manifest and remove/replace non-existent entries.

---

### ISS-007: Low relevance scores (< 0.7)

- **File:** `rag_pipeline.py`, `embed_uploader.py`
- **Description:** Many Pinecone results return with scores around 0.6x, indicating suboptimal embedding quality. Possible causes:
  - Embedding model (EmbeddingGemma-300m) not trained on game-specific data
  - Chunk content contains too much noise (markdown syntax, table formatting)
  - Unbalanced chunk sizes (some 200 chars, some 23KB)
- **Status:** OPEN — Consider benchmarking alternative embedding models or improving chunk preprocessing.

---

## Summary

| ID | Severity | Description | Status |
| --- | --- | --- | --- |
| ISS-001 | CRITICAL | Nav tables in text chunks | OPEN |
| ISS-002 | CRITICAL | LLM hallucination (PSO2/NGS mix) | PARTIAL |
| ISS-003 | CRITICAL | Skill data not searchable | OPEN |
| ISS-004 | MEDIUM | Discord 2000-char limit | FIXED |
| ISS-005 | MEDIUM | Pinecone metadata truncation | FIXED |
| ISS-006 | LOW | 25 Redlink pages | KNOWN |
| ISS-007 | LOW | Low relevance scores | OPEN |
