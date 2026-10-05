"""Shared, terminal-independent entity and action data."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

Pos = tuple[int, int]


@dataclass(frozen=True)
class PlayerColor:
    name: str
    symbol: str
    ansi_index: int

    @property
    def ansi(self) -> str:
        return f"\x1b[38;5;{self.ansi_index}m"


COLORS = (
    PlayerColor("Red", "R", 203), PlayerColor("Blue", "B", 33),
    PlayerColor("Green", "G", 34), PlayerColor("Pink", "P", 213),
    PlayerColor("Orange", "O", 208), PlayerColor("Yellow", "Y", 221),
    PlayerColor("Black", "K", 242), PlayerColor("White", "W", 255),
    PlayerColor("Purple", "U", 141), PlayerColor("Brown", "N", 130),
    PlayerColor("Cyan", "C", 87), PlayerColor("Lime", "L", 118),
)


@dataclass
class Player:
    id: str
    name: str
    symbol: str
    color: str
    pos: Pos
    role: str = "crew"
    alive: bool = True
    vent_id: Optional[str] = None
    goal: Optional[Pos] = None
    path: list[Pos] = field(default_factory=list)
    move_clock: float = 0.0
    rethink_clock: float = 0.0
    kill_clock: float = 0.0
    emergencies_left: int = 1
    suspicion: dict[str, float] = field(default_factory=dict)


@dataclass
class Body:
    victim_id: str
    victim_name: str
    pos: Pos
    room: str
    killed_at: float
    near_at_death: list[str] = field(default_factory=list)


MOVE_ACTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


@dataclass(frozen=True)
class Action:
    kind: str = "wait"
    target: Optional[str] = None
    message: Optional[str] = None

    @classmethod
    def parse(cls, value: object) -> "Action":
        if isinstance(value, cls):
            action = value
        elif isinstance(value, str):
            action = cls(value)
        elif isinstance(value, dict) and set(value) <= {"kind", "target", "message"}:
            action = cls(**value)
        else:
            raise ValueError("Action must be a name or an object with kind, optional target or message")
        if not isinstance(action.kind, str) or action.kind not in (
                *MOVE_ACTIONS, "wait", "interact", "report", "emergency", "kill", "vote", "chat", "vent", "sabotage", "doors"):
            raise ValueError(f"Unknown action: {action.kind!r}")
        if action.target is not None and not isinstance(action.target, str):
            raise ValueError("Action target must be a player/vent ID or null")
        if action.target is not None and action.kind not in ("vote", "kill", "vent", "sabotage", "doors"):
            raise ValueError("Only kill, vote, vent, sabotage and doors actions accept a target")
        if action.kind == "doors" and not isinstance(action.target, str):
            raise ValueError("Doors action requires a room id target")
        if action.kind == "sabotage" and action.target not in ("reactor", "o2", "admin", "lights"):
            raise ValueError("Sabotage target must be reactor, o2, admin or lights")
        if action.kind == "chat":
            if not isinstance(action.message, str) or not action.message.strip():
                raise ValueError("Chat requires a non-empty message string")
        elif action.message is not None:
            raise ValueError("Only chat actions accept a message")
        return action
