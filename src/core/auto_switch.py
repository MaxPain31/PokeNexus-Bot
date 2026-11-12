from dataclasses import dataclass


@dataclass(frozen=True)
class AutoSwitchDecision:
    handled: bool
    slot: int | None = None
    exhausted: bool = False
    turn: int | None = None


class AutoSwitchController:
    """Select the next available Pokemon slot after a confirmed faint."""

    _FIRST_SWITCH_SLOT = 2
    _LAST_SLOT = 6
    _CLICK_DELAY_SECONDS = 2.0

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._next_slot = self._FIRST_SWITCH_SLOT
        self._faint_pending = False
        self._fainted_turn: int | None = None
        self._last_clicked_turn: int | None = None
        self._click_due_at: float | None = None
        self._waiting_for_recovery = False
        self._exhausted_reported = False

    def update(
        self,
        *,
        enabled: bool,
        on_battle_mode: bool,
        health: object,
        turn: object,
        now: float,
    ) -> AutoSwitchDecision:
        if not enabled or not on_battle_mode:
            self.reset()
            return AutoSwitchDecision(handled=False)

        valid_turn = isinstance(turn, int) and not isinstance(turn, bool)
        if not isinstance(health, int) or isinstance(health, bool):
            if not self._faint_pending:
                return AutoSwitchDecision(handled=self._waiting_for_recovery)
            if self._click_due_at is None:
                self._click_due_at = now + self._CLICK_DELAY_SECONDS
                return AutoSwitchDecision(handled=True)
            if now < self._click_due_at:
                return AutoSwitchDecision(handled=True)
            click_turn = turn if valid_turn else self._fainted_turn
            if click_turn is None:
                return AutoSwitchDecision(handled=True)
            return self._ready_decision(click_turn)

        if health > 0:
            if self._waiting_for_recovery:
                self._waiting_for_recovery = False
            self._faint_pending = False
            self._fainted_turn = None
            self._click_due_at = None
            self._exhausted_reported = False
            return AutoSwitchDecision(handled=False)

        if self._waiting_for_recovery:
            return AutoSwitchDecision(handled=True)

        if not valid_turn:
            return AutoSwitchDecision(handled=True)

        if not self._faint_pending:
            self._faint_pending = True
            self._fainted_turn = turn
            self._click_due_at = now + self._CLICK_DELAY_SECONDS

        if self._click_due_at is None:
            self._click_due_at = now + self._CLICK_DELAY_SECONDS
            return AutoSwitchDecision(handled=True)

        if now < self._click_due_at:
            return AutoSwitchDecision(handled=True)

        return self._ready_decision(turn)

    def _ready_decision(self, turn: int) -> AutoSwitchDecision:
        if self._next_slot > self._LAST_SLOT:
            should_report = not self._exhausted_reported
            self._exhausted_reported = True
            return AutoSwitchDecision(handled=True, exhausted=should_report)

        if self._last_clicked_turn == turn:
            return AutoSwitchDecision(handled=True)

        return AutoSwitchDecision(
            handled=True,
            slot=self._next_slot,
            turn=turn,
        )

    def record_click(self, slot: int, turn: int, succeeded: bool) -> None:
        if not succeeded:
            self._click_due_at = None
            return
        if slot != self._next_slot or not (
            self._FIRST_SWITCH_SLOT <= slot <= self._LAST_SLOT
        ):
            raise ValueError(f"Invalid auto-switch slot: {slot}")

        self._last_clicked_turn = turn
        self._next_slot = slot + 1
        self._waiting_for_recovery = True
        self._faint_pending = False
        self._fainted_turn = None
        self._click_due_at = None
