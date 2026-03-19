# Core Features Details

The project is not just a standard automated chatbot, but a **Versatile Virtual Assistant** tailored for the Phantasy Star Online 2 community.

## 1. AI Game Advisor

This is the core feature helping both new and veteran players approach the game more easily.
- **Class Build Advice**:
  - User asks: *"How to play the strongest Slayer in the current version?"*
  - The Bot will look up priority Skill Trees, recommended weapons (Gunblade), and meta-aligned Augments from the Wiki.
- **Mechanics Explanation**:
  - Answers complex questions about Damage formulas, Status Ailments mechanics, or how Add-on Skills work.
  - Always includes Citations so players can read more on the Wiki.

## 2. Reverse Look-up Phashion (Image-based Fashion Search)

A breakthrough feature for the "End-game Fashion" community:
- **Problem**: A player browsing Twitter/X sees a beautiful cosplay or outfit post but the poster didn't list the items.
- **Solution**:
  1. User sends or replies to that image to the Bot with the `/phashion` command.
  2. **AI Vision Agent** (Using Gemini 1.5 Flash) will "look" at the image, extracting details: Colors, Hairstyles, Accessories, Cast part characteristics (Arms, Legs, Body).
  3. Based on the description gathered, the Bot **queries RAG** against the Phashion database scraped from the Wiki.
  4. The Bot returns the top 3-5 items with the highest match, including icon images from the Wiki for the user to compare.

## 3. Persona Role Play (Immersive Conversation)

To increase entertainment and user engagement, the Bot doesn't reply in a dry, robotic manner.
- **Persona System (Personality Masks)**:
  - Admins can switch the Bot's Persona for each server (Guild/Alliance).
  - Ex: Server 1 selects **Matoi** (Innocent, caring, calls user "Guardian"). Server 2 selects **Xiera** (Energetic, smart, likes to joke).
- **Advanced Contextual Memory (2-Layer)**:
  - Integrates **2-Layer Memory (Short-term & Long-term)** combined with automatic Context Compression so the bot can hold long conversations without incurring massive costs.
  - Integrates **Emotion State Machine** for the bot to track Mood (Happy, sad, angry) and Trust levels towards the User, making responses feel more "human".
  
*RP mechanism details:* [08-rp-memory-management.md](08-rp-memory-management.md)
