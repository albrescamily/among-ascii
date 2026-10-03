"""Structured, bounded event history with explicit per-player visibility."""
from __future__ import annotations
from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class Event:
    sequence: int
    time: float
    kind: str
    message: str
    actor: Optional[str]
    target: Optional[str]
    data: dict
    visible_to: Optional[tuple[str, ...]]

    def to_dict(self) -> dict:
        return asdict(self)


class EventLog:
    def __init__(self, capacity: int) -> None:
        self.history: deque[Event] = deque(maxlen=capacity)
        self.sequence = 0

    def emit(self, time: float, kind: str, message: str, *, actor: Optional[str] = None,
             target: Optional[str] = None, data: Optional[dict] = None,
             visible_to: Optional[list[str]] = None) -> Event:
        self.sequence += 1
        event = Event(self.sequence, round(time, 6), kind, message, actor, target,
                      deepcopy(data or {}), tuple(visible_to) if visible_to is not None else None)
        self.history.append(event)
        return event

    def for_player(self, player_id: str, after: int = 0) -> list[dict]:
        result = []
        for event in self.history:
            if event.sequence > after and (event.visible_to is None or player_id in event.visible_to):
                public = event.to_dict()
                public.pop("visible_to")  # Recipient lists can reveal unseen witnesses.
                result.append(public)
        return result

    def snapshot(self, after: int = 0) -> list[dict]:
        """Privileged history for experiment logging; never included in normal observations."""
        return [event.to_dict() for event in self.history if event.sequence > after]
