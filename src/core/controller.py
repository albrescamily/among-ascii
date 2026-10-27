"""The contract between the engine and whatever decides for players: NPCs, agents, scripts."""
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol
if TYPE_CHECKING:
    from .engine import Game


class Controller(Protocol):
    """Decides for some players. The engine calls these hooks at fixed points of a match;
    new controllers should act only through `Game.apply_action`."""

    def tick(self, game: Game) -> None:
        """Once per simulation step while the round is running."""

    def meeting_started(self, game: Game) -> None:
        """Right after a meeting is called, once its `meeting_called` event is out."""

    def meeting_tick(self, game: Game) -> None:
        """Once per tick while a meeting is open."""
