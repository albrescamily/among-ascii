"""Bounded public chat shared by players and the terminal."""
from __future__ import annotations
from collections import deque
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .engine import Game


@dataclass(frozen=True)
class ChatMessage:
    sequence: int
    time: float
    round: int
    sender: str
    color: str
    text: str


class ChatSystem:
    def __init__(self, history: int, max_length: int) -> None:
        self.messages: deque[ChatMessage] = deque(maxlen=history)
        self.max_length = max_length
        self.sequence = 0

    def send(self, game: "Game", sender_id: str, message: str) -> bool:
        actor = game.entity(sender_id)
        if not actor.alive or game.outcome or not isinstance(message, str):
            return False
        # Never allow chat text to execute terminal escapes or add rows.
        clean = " ".join("".join(char if char.isprintable() else " " for char in message).split())
        if not clean or len(message) > self.max_length:
            return False
        self.sequence += 1
        entry = ChatMessage(self.sequence, round(game.elapsed, 6), game.meeting_number,
                            actor.id, actor.name, clean)
        self.messages.append(entry)
        game.emit("chat_message", f"{actor.name}: {clean}", actor=actor.id,
                  data=asdict(entry))
        return True

    def snapshot(self) -> list[dict]:
        # Copies protect the stored history from callers.
        return [asdict(message) for message in self.messages]
