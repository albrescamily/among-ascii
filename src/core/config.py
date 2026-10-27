"""Validated game settings, loaded from JSON or constructed in Python."""
from __future__ import annotations
import json
import math
from dataclasses import asdict, dataclass, fields
from functools import cache
from pathlib import Path
from typing import Optional, get_type_hints
from .models import COLORS
from .world import TASK_POSITIONS


# Interactive setup ranges; GameConfig itself accepts smaller rosters and longer cooldowns.
MENU_LIMITS = {
    "player_count": (4, len(COLORS)),
    "kill_cooldown": (0, 60),
    "tasks_per_player": (0, len(TASK_POSITIONS)),
    "discussion_seconds": (0, 120),
    "voting_seconds": (5, 120),
}

# Each field is type-checked from its annotation; floats also accept ints, never booleans.
TYPE_CHECKS = {
    bool: lambda value: type(value) is bool,
    int: lambda value: type(value) is int,
    Optional[int]: lambda value: value is None or type(value) is int,
    float: lambda value: type(value) in (int, float) and math.isfinite(value),
    str: lambda value: isinstance(value, str),
}


@cache
def field_types() -> dict[str, object]:
    """Resolved GameConfig annotations, computed once instead of on every instance."""
    return get_type_hints(GameConfig)


@dataclass(frozen=True)
class GameConfig:
    seed: Optional[int] = None  # None: a fresh random seed every match (tasks, impostors)
    play_mode: str = "game"  # simulation is an inactive scaffold
    test_mode: bool = False  # Game-mode sandbox: idle NPCs, role swap (X), reset (N)
    allow_god_view: bool = True  # Caps Lock God view in Game mode; forced on by test_mode
    player_count: int = 12
    player_color: str = "Cyan"
    player_role: str = "random"  # crew, impostor, or random (the human may be an impostor)
    impostor_count: int = 1
    tasks_per_player: int = 5
    task_seconds: float = 1.8
    task_win_mode: str = "player"  # player, team (living crew), disabled
    crew_move_seconds: float = 0.34
    impostor_move_seconds: float = 0.29
    player_move_seconds: float = 0.12
    vision_radius: int = 7
    lights_vision_radius: int = 2
    kill_radius: int = 1
    kill_cooldown: float = 10.0
    initial_kill_cooldown: float = 9.0
    post_meeting_kill_cooldown: float = 6.0
    hunt_delay: float = 5.0
    report_delay: float = 0.6
    emergencies_per_player: int = 1
    meetings_enabled: bool = True
    discussion_seconds: float = 15.0  # meeting opens with talk only; ballots open afterwards
    voting_seconds: float = 30.0
    vote_result_seconds: float = 8.0  # how long the tally stays up (any key skips it)
    chat_history: int = 100
    chat_max_length: int = 200
    kills_enabled: bool = True
    sabotage_enabled: bool = True
    sabotage_seconds: float = 40.0
    sabotage_repair_seconds: float = 3.0
    sabotage_cooldown: float = 20.0
    initial_sabotage_cooldown: float = 25.0
    max_closed_rooms: int = 3
    door_close_seconds: float = 10.0
    door_cooldown: float = 10.0  # per room, after it reopens
    initial_door_cooldown: float = 10.0  # before any door can be closed
    npc_ai_enabled: bool = True
    end_on_player_death: bool = True
    max_seconds: float = 600.0
    event_history: int = 2000
    fps: int = 15

    def __post_init__(self) -> None:
        hints = field_types()
        for field in fields(self):
            check = TYPE_CHECKS.get(hints[field.name])
            if check is None:
                raise TypeError(f"GameConfig.{field.name} has an unsupported annotation: {hints[field.name]!r}")
            if not check(getattr(self, field.name)):
                raise ValueError(f"Invalid type or non-finite value for {field.name}")
        if not 1 <= self.player_count <= len(COLORS):
            raise ValueError(f"player_count must be between 1 and {len(COLORS)}")
        if self.play_mode not in {"game", "simulation"}:
            raise ValueError("play_mode must be game or simulation")
        if not 0 <= self.impostor_count < self.player_count:
            raise ValueError("impostor_count must be smaller than player_count")
        if self.impostor_count and self.impostor_count * 2 >= self.player_count:
            raise ValueError("Crew must outnumber impostors at the start")
        if self.player_color not in {color.name for color in COLORS}:
            raise ValueError("player_color must be an English color from COLORS")
        if self.player_role not in {"crew", "impostor", "random"}:
            raise ValueError("player_role must be crew, impostor, or random")
        if self.player_role == "impostor" and not self.impostor_count:
            raise ValueError("An impostor player requires at least one impostor")
        if not 0 <= self.tasks_per_player <= len(TASK_POSITIONS):
            raise ValueError(f"tasks_per_player must be between 0 and {len(TASK_POSITIONS)}")
        if self.task_win_mode not in {"player", "team", "disabled"}:
            raise ValueError("task_win_mode must be player, team, or disabled")
        for name in ("task_seconds", "crew_move_seconds", "impostor_move_seconds", "player_move_seconds",
                     "max_seconds", "event_history", "fps", "vision_radius", "lights_vision_radius",
                     "voting_seconds", "vote_result_seconds", "chat_history", "chat_max_length", "sabotage_seconds", "sabotage_repair_seconds",
                     "door_close_seconds", "max_closed_rooms"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("kill_radius", "kill_cooldown", "initial_kill_cooldown", "post_meeting_kill_cooldown",
                     "hunt_delay", "report_delay", "emergencies_per_player", "sabotage_cooldown", "initial_sabotage_cooldown", "door_cooldown", "discussion_seconds",
                     "initial_door_cooldown"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.fps > 120 or self.vision_radius > 100:
            raise ValueError("Limits: fps <= 120, vision_radius <= 100")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GameConfig":
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a JSON object")
        unknown = set(data) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
        return cls(**data)

    @classmethod
    def load(cls, path: str | Path) -> "GameConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
