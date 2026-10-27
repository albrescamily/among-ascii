"""The local player's controller: their intents go through the same rules as NPCs and agents."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from ..core.engine import TICK_STEP
from ..core.models import EPSILON, MOVE_ACTIONS, Action, Player

if TYPE_CHECKING:
    from ..core.engine import Game


def acting_impostor(game: Game) -> Optional[Player]:
    """Who sabotages and closes doors for the player: the local player while a living impostor,
    or the first living impostor for the simulation spectator."""
    if game.config.play_mode == "simulation":
        return next((actor for actor in game.players if actor.alive and actor.role == "impostor"), None)
    return game.player if game.player.role == "impostor" and game.player_alive else None


class PlayerController:
    """Applies the local player's actions with `Game.apply_action`, so move cooldowns hold.

    A move asked during the cooldown is buffered (the latest one wins) and retried every frame
    until the cooldown ends, so a key pressed slightly early is not lost. Cooldowns run in whole
    simulation steps, so a buffered move may wait up to one move interval plus one step."""

    def __init__(self) -> None:
        self.buffered_move: Optional[str] = None
        self.buffered_at = 0.0  # game.elapsed when the move was buffered
        self.buffered_round = 0  # meeting number then: a meeting discards the buffer

    def act(self, game: Game, action: Action | str | dict) -> bool:
        action = Action.parse(action)
        if action.kind in MOVE_ACTIONS and game.player.move_clock > EPSILON:
            self.buffered_move = action.kind
            self.buffered_at, self.buffered_round = game.elapsed, game.meeting_number
            return False
        if action.kind in MOVE_ACTIONS:
            self.buffered_move = None
        return game.apply_action(game.player_id, action)

    def update(self, game: Game) -> None:
        """Once per frame, after the simulation advanced: retry a buffered move when ready."""
        if self.buffered_move is None:
            return
        expired = (game.meeting_number != self.buffered_round
                   or game.elapsed - self.buffered_at > game.move_interval(game.player) + TICK_STEP + EPSILON)
        if expired:
            self.buffered_move = None
        elif game.player.move_clock <= EPSILON:
            kind, self.buffered_move = self.buffered_move, None
            game.apply_action(game.player_id, kind)

    def sabotage(self, game: Game, kind: str) -> bool:
        """Players sabotage as themselves; the simulation spectator through a living impostor."""
        actor = acting_impostor(game) if game.config.play_mode == "simulation" else game.player
        if actor is None:
            game.message("Sabotage unavailable: no living impostor.")
            return False
        if game.apply_action(actor.id, {"kind": "sabotage", "target": kind}):
            return True
        game.message("Sabotage requires a living impostor, no active emergency and a ready cooldown.")
        return False

    def close_doors(self, game: Game, room_id: str) -> bool:
        actor = acting_impostor(game)
        if actor is None:
            game.message("Doors unavailable: no living impostor.")
            return False
        reason = game.doors.refusal(game, actor.id, room_id)
        if reason:
            game.message(reason)
            return False
        return game.apply_action(actor.id, {"kind": "doors", "target": room_id})
