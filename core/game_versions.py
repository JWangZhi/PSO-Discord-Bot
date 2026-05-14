"""Game-version resolution for Base/NGS/Both wiki queries."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.memory import MemoryManager


VERSION_KEYWORDS_NGS = frozenset({
    "ngs", "new genesis", "retem", "kvaris", "stia", "halpha",
    "slayer", "waker", "stellar blade", "duel blade",
    "cocoon", "tower", "trinia", "aelio", "leciel",
    "add-on skill", "fixa",
})
VERSION_KEYWORDS_PSO2 = frozenset({
    "pso2", "classic", "base", "base game",
    "phantom", "hero", "etoile", "luster", "summoner",
    "premium set", "dark blast", "mag evolution",
    "matter board", "swap shop",
})

GAME_LABELS = {"ngs": "NGS", "pso2": "Base", "both": "Both"}
CODE_VERSION = "wiki-grounding-2026-05-12-2"
SHARED_GAME_CLASSES = frozenset({
    "hunter", "fighter", "ranger", "gunner", "force", "techter", "braver", "bouncer",
})


def game_label(game_key: str) -> str:
    """Return the user-facing game label for an internal game key."""
    return GAME_LABELS.get(game_key, game_key)


class GameVersionResolver:
    """Resolve whether a wiki query targets NGS, Base, or Both."""

    ASK_MSG = (
        "📋 Just to make sure I pull the right data — are you asking about "
        "**NGS**, **Base**, or **Both**?\n"
        "Reply with **NGS**, **Base**, or **Both**. I will remember NGS/Base for this channel."
    )
    SWITCH_MSG = (
        "🔄 It looks like you're switching from **{old}** to **{new}**. "
        "Should I start a fresh session for {new}? "
        "Reply **yes** to reset, or **no** to keep the current history."
    )
    VERSION_LABELS = GAME_LABELS

    def __init__(self, memory: "MemoryManager"):
        self._memory = memory

    def _hard_detect(self, text: str) -> str | None:
        """Return 'ngs'/'pso2' for unambiguous keyword hits."""
        lower = text.lower()
        ngs_hit = any(k in lower for k in VERSION_KEYWORDS_NGS)
        pso2_hit = any(k in lower for k in VERSION_KEYWORDS_PSO2)
        if ngs_hit and not pso2_hit:
            return "ngs"
        if pso2_hit and not ngs_hit:
            return "pso2"
        return None

    def _parse_clarification(self, text: str) -> str | None:
        """Parse a user reply to the clarification prompt."""
        t = text.strip().lower()
        if t in {"ngs", "new genesis", "ngs!", "ngs."}:
            return "ngs"
        if t in {"pso2", "classic", "base", "base game", "pso2 classic", "pso2!"}:
            return "pso2"
        if t in {"both", "both!", "both.", "all", "compare", "comparison", "base and ngs", "ngs and base"}:
            return "both"
        return None

    def _parse_switch_reply(self, text: str) -> bool | None:
        """Parse yes/no reply to a version-switch prompt."""
        t = text.strip().lower()
        if t in {"yes", "y", "yeah", "yep", "sure", "ok", "reset", "new"}:
            return True
        if t in {"no", "n", "nope", "keep", "nah"}:
            return False
        return None

    @staticmethod
    def _mentions_shared_class(text: str) -> bool:
        """Return True for classes that exist in both Base and NGS."""
        lower = text.lower()
        return any(re.search(rf"\b{re.escape(cls)}\b", lower) for cls in SHARED_GAME_CLASSES)

    async def resolve(
        self,
        session_id: str,
        message_text: str,
        router_suggestion: str,
    ) -> tuple[str | None, str | None]:
        """Resolve the game version for a wiki query.

        Returns:
            (version, pending_action)
            - version: "ngs" | "pso2" | "both" | None
            - pending_action: "ask_version" | "ask_switch:{old}:{new}" | None
        """
        hard = self._hard_detect(message_text)
        saved = await self._memory.get_game_version_pref(session_id)

        if hard:
            if saved and saved != hard:
                return None, f"ask_switch:{saved}:{hard}"
            if not saved:
                await self._memory.set_game_version_pref(session_id, hard)
            return hard, None

        if saved in {"ngs", "pso2", "both"}:
            return saved, None

        if self._mentions_shared_class(message_text):
            return None, "ask_version"

        if router_suggestion == "pso2":
            await self._memory.set_game_version_pref(session_id, "pso2")
            return "pso2", None

        return None, "ask_version"
