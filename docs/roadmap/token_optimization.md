# Token Optimization Strategy

Based on the "Overclocking AI Agent" principle, the system will apply 4 main optimization vectors to reduce token costs by 60% without decreasing the Bot's intelligence.

---

## 1. Fast Route Bypass
Avoid abusing the LLM for simple commands or spam messages.

- **Mechanism**: Use Regex + basic logic to classify messages before sending them to the AI.
- **Application**: 
  - Casual greetings ("hi", "hello") -> Reply using pre-defined Persona templates.
  - Purely system commands (`/check_eq`, `/link_wiki`) -> Route directly to the command handler module, bypassing the AI Router.
- **Benefit**: Saves 100% of tokens for about 70% of regular message volume.

---

## 2. Intent Matrix - Lazy Memory Injection
Do not cram the entire RAG data into every interaction.

- **Mechanism**: Tier the data loading level based on user intent.
- **Tiers**:
  - **Casual Chat**: Only load one line of the character's Profile (~20 tokens).
  - **General Question**: Load brief snippets from the Vector DB related to the question.
  - **Deep Dive/Build Advice**: Only trigger Full RAG Retrieval when the user asks for in-depth technical advice.
- **Benefit**: Reduces 300-800 tokens per request for typical conversations.

---

## 3. Tiered Prompting
Break down the massive System Prompt into streamlined versions.

- **Tier Structure**:
  - **MINI Tier (~200 tokens)**: For heavy logic tasks, contains only the core rules.
  - **STANDARD Tier**: Contains instructions on Persona and basic interaction guidelines.
  - **FULL Tier**: Contains the entire list of Actions, error handling rules, and complex logic.
- **Benefit**: Reduces from 1200 down to 200 tokens when processing pure logic cases.

---

## 4. History Compression & Context Caching
Smart chat history management.

- **3-Zone History Structure**:
  - **Recent Zone**: Kept intact to maintain continuity.
  - **Middle Zone**: Truncate long Bot responses, remove executed [ACTION] tags to avoid repeating mistakes.
  - **Old Zone**: Summarized into a single line (Summarization).
- **Gemini Context Caching**: Push static System Prompts and rarely changing Lore data into Google's `cachedContent` to pay for storage rather than input tokens on every call.

---

## 5. Conclusion
Every token wasted on "garbage" context is a useless expense. The system will prioritize spending tokens on the AI's "reasoning" rather than loading redundant data.
