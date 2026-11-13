import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from core.battle_action import ACTION_RETRY_SECONDS


def special_pokemon_message_kind(text: str) -> str | None:
    """Recognize the encounter dialog, including its generic in-game wording."""
    for pattern, kind in (
        (r"\bshiny\b", "Shiny"),
        (r"\bextremely[\s-]+rare\b", "Extremely Rare"),
        (r"\blegendary\b", "Legendary"),
        (r"\bspecial\s+encounter\b", "Special Encounter"),
        (
            r"\byou\s+have\s+encountered\s+a\s+special\s+pok[eé]mon\b",
            "Special Encounter",
        ),
        (r"\bclick\s+the\s+close\s+button\s+to\s+continue\b", "Special Encounter"),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            return kind
    return None


def has_special_pokemon_message_hint(text: str) -> bool:
    """Recognize partly garbled dialog wording without authorizing a close click."""
    words = re.findall(r"[a-z]+", text.casefold().replace("é", "e"))
    hints = (
        ("special", "encounter"), ("special", "pokemon"),
        ("shiny", "pokemon"), ("extremely", "rare"), ("legendary", "pokemon"),
        ("close", "button"), ("auto", "close"),
    )
    return any(
        all(
            SequenceMatcher(None, observed, expected).ratio() >= 0.75
            for observed, expected in zip(pair, hint)
        )
        for pair in zip(words, words[1:])
        for hint in hints
    )


@dataclass(frozen=True)
class SpecialPokemonMessageDecision:
    handled: bool
    click_close: bool = False
    detected: bool = False
    dismissed: bool = False


class SpecialPokemonMessageController:
    """Block battle inputs until a detected encounter popup is visibly closed."""

    _ABSENCE_CONFIRMATION_POLLS = 2

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.blocked = False
        self._detected = False
        self._last_frame_at = 0.0
        self._next_click_at = 0.0
        self._absent_polls = 0

    def update(
        self,
        *,
        enabled: bool,
        visible: object,
        frame_at: float,
    ) -> SpecialPokemonMessageDecision:
        if not enabled:
            self.reset()
            return SpecialPokemonMessageDecision(handled=False)
        if frame_at <= self._last_frame_at:
            return SpecialPokemonMessageDecision(
                handled=self.blocked or visible is not False,
            )
        self._last_frame_at = frame_at

        if visible is True:
            detected = not self._detected
            self._detected = True
            self.blocked = True
            self._absent_polls = 0
            return SpecialPokemonMessageDecision(
                handled=True,
                click_close=frame_at >= self._next_click_at,
                detected=detected,
            )
        if visible is not False:
            self.blocked = True
            self._absent_polls = 0
            return SpecialPokemonMessageDecision(handled=True)

        if self.blocked:
            self._absent_polls += 1
            if self._absent_polls < self._ABSENCE_CONFIRMATION_POLLS:
                return SpecialPokemonMessageDecision(handled=True)
            dismissed = self._detected
            self._detected = False
            self.blocked = False
            self._next_click_at = 0.0
            self._absent_polls = 0
            return SpecialPokemonMessageDecision(handled=False, dismissed=dismissed)

        self.blocked = False
        return SpecialPokemonMessageDecision(handled=False)

    def record_attempt(self, completed_at: float) -> None:
        # Pace successful and failed mouse attempts alike. Only a capture
        # started after this attempt may trigger another click or confirm close.
        self._next_click_at = completed_at + ACTION_RETRY_SECONDS
        self._last_frame_at = max(self._last_frame_at, completed_at)
        self._absent_polls = 0
