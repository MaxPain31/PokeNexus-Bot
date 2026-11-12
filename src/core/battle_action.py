import re
from dataclasses import dataclass, field

from core.auto_battle import has_complete_move_set


ACTION_BUTTON_ROLES = {
    "fight": "fight button",
    "bag": "bag button",
    "swap": "pokemon/swap button",
    "run": "run button",
}
ACTION_BUTTON_STATES = {
    "fight": ("clickedFightButton", "showingFightButton"),
    "bag": ("clickedBagButton", "showingBagButton"),
    "swap": ("clickedSwapButton", "showingPokemonButton"),
    "run": ("clickedRunButton", "showingRunButton"),
}
ACTION_RETRY_SECONDS = 2.0
BATTLE_ACTION_KEYS = {
    ("fight", "fight button"): "1",
    ("bag", "bag button"): "2",
    ("swap", "pokemon/swap button"): "3",
    ("run", "run button"): "4",
    ("bag", "previous button"): "1",
    ("bag", "next button"): "2",
    ("bag", "use item button"): "3",
    **{("moves", f"move slot {slot}"): str(slot) for slot in range(1, 5)},
    **{
        ("pokemon-team", f"pokemon team slot {slot}"): str(slot)
        for slot in range(2, 7)
    },
}


def action_menu_visible(state: dict[str, object]) -> bool:
    return all(state.get(visible) is True for _, visible in ACTION_BUTTON_STATES.values())


def has_move_details(text: object) -> bool:
    return isinstance(text, str) and re.search(r"\b\d+\s*/\s*\d+\b", text) is not None


def _item_name(state: dict[str, object]) -> str:
    name = state.get("bagItemName")
    return re.sub(r"\s+", " ", name).strip().casefold() if isinstance(name, str) else ""


def bag_menu_visible(state: dict[str, object]) -> bool:
    """Require fresh item-menu evidence, not just a remembered Bag input."""
    if (
        state.get("onBattleMode") is not True
        or state.get("clickStateReliable") is False
        or action_menu_visible(state)
        or not _item_name(state)
    ):
        return False
    detected = state.get("showingBagItemMenu")
    if isinstance(detected, bool):
        return detected
    # Older/synthetic states can identify the Bag from a readable item and
    # hidden main buttons, provided they do not describe another submenu.
    return (
        all(state.get(visible) is False for _, visible in ACTION_BUTTON_STATES.values())
        and not has_move_details(state.get("bagItemName"))
        and not has_move_details(state.get("pokemonMoves"))
        and not any(
            state.get(clicked) is True
            for clicked in ("clickedFightButton", "clickedSwapButton", "clickedRunButton")
        )
    )


@dataclass
class PendingBattleAction:
    """An input attempt whose result must be observed in a newer game frame."""

    feature: str
    turn: int | None
    action_name: str
    automation_name: str
    submitted_at: float
    before: dict[str, object]
    settle_delay: float | None = None
    last_attempted_at: float = field(init=False)
    _last_frame_at: float = field(default=0.0, init=False)
    _response_polls: int = field(default=0, init=False)
    _non_battle_polls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.mark_submitted(self.submitted_at)

    def mark_submitted(self, submitted_at: float) -> None:
        self.submitted_at = submitted_at
        self.last_attempted_at = submitted_at
        self._response_polls = 0
        self._non_battle_polls = 0

    def observe(
        self,
        state: dict[str, object],
        frame_at: float,
        *,
        retry_delay_seconds: float = ACTION_RETRY_SECONDS,
    ) -> str:
        if frame_at <= self.submitted_at or frame_at <= self._last_frame_at:
            return "waiting"
        self._last_frame_at = frame_at
        if state.get("clickStateReliable") is False:
            self._response_polls = 0
            self._non_battle_polls = 0
            return "waiting"
        feature = self.feature
        role = self.action_name.casefold()
        opens_menu = role == ACTION_BUTTON_ROLES.get(feature) and feature != "run"
        browses_items = feature == "bag" and role in ("previous button", "next button")

        if state.get("onBattleMode") is not True:
            self._response_polls = 0
            self._non_battle_polls = (
                self._non_battle_polls + 1 if state.get("onBattleMode") is False else 0
            )
            if self._non_battle_polls >= 3:
                return "cancelled" if opens_menu or browses_items else "confirmed"
            return "waiting"
        self._non_battle_polls = 0

        turn = state.get("turn")
        if (
            isinstance(turn, int) and not isinstance(turn, bool)
            and self.turn is not None and turn != self.turn
            and feature != "pokemon-team"
        ):
            return "cancelled" if opens_menu or browses_items else "confirmed"

        menu_visible = action_menu_visible(state)
        menu_absent = all(
            state.get(visible) is False for _, visible in ACTION_BUTTON_STATES.values()
        )
        responded = False
        unchanged = False
        if role == ACTION_BUTTON_ROLES.get(feature):
            clicked, _visible = ACTION_BUTTON_STATES[feature]
            if feature == "bag" and bag_menu_visible(state):
                return "confirmed"
            if feature != "bag" and state.get(clicked) is True and menu_absent:
                return "confirmed"
            unchanged = menu_visible
        elif feature == "moves":
            moves = state.get("pokemonMoves")
            if menu_visible:
                return "cancelled"
            # A missing OCR line alone is not a completed move. Observe the
            # animation/menu transition in consecutive frames before advancing.
            responded = menu_absent and not has_move_details(moves)
            unchanged = (
                menu_absent and state.get("clickedFightButton") is True
                and isinstance(moves, str) and has_complete_move_set(moves)
            )
        elif browses_items:
            item = _item_name(state)
            if menu_visible:
                return "cancelled"
            if bag_menu_visible(state) and item != _item_name(self.before):
                return "confirmed"
            unchanged = bag_menu_visible(state)
        elif role == "use item button":
            item = _item_name(state)
            responded = menu_visible or (menu_absent and not item)
            if bag_menu_visible(state):
                if item != _item_name(self.before):
                    return "cancelled"
                unchanged = True
        elif feature == "pokemon-team":
            health = state.get("yourPokemonHealth")
            if isinstance(health, int) and not isinstance(health, bool) and health > 0:
                return "confirmed"
            unchanged = menu_absent and health == 0

        self._response_polls = self._response_polls + 1 if responded else 0
        if self._response_polls >= 2:
            return "confirmed"
        # Even with no keyboard cooldown, retry only from a newer, readable
        # frame showing that the same menu is still ready for this action.
        if unchanged and frame_at >= self.last_attempted_at + retry_delay_seconds:
            return "retry"
        return "waiting"
