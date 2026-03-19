# AI Models & Overclocking Strategy

This design uses the "Overclocking AI Agent" technique, aiming for maximum performance with $0 API costs by thoroughly optimizing how the AI is called.

## 1. Core Models Selection

We apply a **Dual-Engine** architecture leveraging Free Tier APIs:

- **Google Gemini 1.5 Flash**: 
  - *Role*: The heart of the RAG and Vision systems.
  - *Reasoning*: Supports a Context Window up to 1 million tokens (can fit an entire book), excellent image analysis capabilities (for the Phashion feature), and free 1,500 requests/day.
- **Groq Llama 3 (70B/8B)**:
  - *Role*: Master Orchestrator and handles short conversations, high-speed Roleplay chats.
  - *Reasoning*: Extremely fast token generation speed (hundreds of tokens/sec), reducing bot sluggishness when it has to think. Excellent flow routing.

## 2. Token Optimization Strategy (Overclocking)

How to play big with AI without spending money?

- **Intent Matrix (Lazy Memory Injection)**: 
  - Instead of always cramming the entire chat history and heavy System Prompts into every question, the Bot evaluates if it's necessary. 
  - Example: Greeting "Hello" -> Triggers tiny Prompt. Asking "Class build" -> Triggers massive Prompt.
- **Tiered Prompting**: Shrink System Prompts into multiple tiers.
- **Context Caching (Gemini)**: Files defining the Database or core game rules rarely change. Upload them to Gemini Cache once, and subsequent questions just reuse them -> Saves 80% on Input Tokens.

## 3. Anti-Hallucination

This is a vital issue for an information-providing Bot (Advisor):

1. **Strict Output Constraints**: Prompts clearly dictate: `"You are an Arks Librarian. YOU ARE NOT ALLOWED to fabricate stats, item names, or skills. If the provided data doesn't contain the answer, confess your lack of knowledge."`
2. **Citation Requirement**: A valid answer must contain at least one link (URL) or standard name to the Wiki page it based its answer on.
3. **Verification Loop**: Agent responses are logically checked one last time by a lightweight Regex/Script before outputting to Discord to prevent format errors.
