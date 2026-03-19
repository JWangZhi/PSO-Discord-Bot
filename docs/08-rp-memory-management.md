# RP Memory & Cost Management

Based on standard documentation for "Cost Optimization & Memory Management", the Role Play (RP) system of the PSO2 Bot will be designed with a **2-Layer Memory** architecture and **Context Compression** to solve the problem: *The longer the conversation, the more expensive and forgetful the AI becomes.*

## 1. 2-Layer Memory Architecture

Absolutely do not cram the entire chat history into the AI. We divide memory into 2 storage layers (Using MongoDB):

### A. Short-term Memory
- **Mechanism**: Only explicitly holds the text of the **last 4-6 messages**.
- **Purpose**: So the AI can immediately understand the Context of the past few seconds/minutes (e.g., what the user just asked).

### B. Long-term Memory
Always accompanies the Bot, but in a Compressed format:
- **Summary**: Summarizes the story's progression (Example: *"The player is stuck in the sewer, Matoi is trying to find a way out"*).
- **Facts**: Immutable information (Example: User's name, Current Class, Equipped Weapon).
- **Character State**: The bot's current state (Emotions, trust level).

## 2. Context Compression Workflow

To prevent the Context from bloating, the system will have a background "Trigger":

1. When `len(recent_messages) > 10`:
2. Send these 10 messages to a small Model (Groq) with the Prompt: *"Summarize the main events, user intent, and emotions in under 100 words"*.
3. Clear older messages from Short-term, keeping only the 4-6 newest ones.
4. Update the summary into the Long-term `Summary`.

**Result**: The Context fed into the LLM is permanently locked under < 1000 Tokens even if the user chats all day.

## 3. Selective Context Injection System

Every time the LLM is called to respond to a chat, the Prompt will be intelligently assembled:
```yaml
[System Persona]: "You are Matoi, loyal and sweet."
[Emotion State]: "Mood: Worried. Trust: 0.8"
[Relevant Facts]: "User is playing Hunter class." (Automatically filtered based on user keywords)
[Summary]: "Trapped in dungeon."
[Recent Messages]: [User: "Help me!", Matoi: "Hold on!"]
```

## 4. Other Optimization Techniques
- **Hybrid Routing**: Route casual social messages (70%) to Groq for ultra-fast processing. Messages requiring image analysis or deep emotional reasoning (30%) go to Gemini Flash.
- **Dynamic Token Control**: Customize `max_tokens`. Normal chat: 100 tokens. Storytelling: 300 tokens.
- **Cache Layer (Redis/MongoDB In-memory)**: Pre-save common greetings and FAQs. Exact matches are served instantly, reducing LLM calls by 10-30%.
