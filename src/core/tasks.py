"""Task catalog, per-player assignments, interaction, and timed progress."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional
from .models import Pos
if TYPE_CHECKING:
    from .engine import Game

TASK_NAMES = {
    (10, 7): "Align upper engine", (20, 10): "Fuel upper engine",
    (40, 4): "Download cafeteria data", (58, 3): "Empty garbage",
    (74, 7): "Clear asteroids", (83, 10): "Download weapons data",
    (28, 17): "Inspect sample", (35, 20): "Submit scan",
    (3, 18): "Start reactor", (10, 24): "Unlock manifolds",
    (19, 23): "Check cameras", (72, 20): "Clean O2 filter",
    (88, 18): "Chart course", (93, 25): "Stabilize steering",
    (64, 17): "Swipe card", (57, 22): "Upload data",
    (34, 26): "Calibrate distributor", (39, 30): "Fix wiring",
    (48, 30): "Fill fuel can", (58, 37): "Empty chute",
    (10, 33): "Align lower engine", (20, 36): "Fuel lower engine",
    (83, 36): "Prime shields", (65, 39): "Download comms data",
}
TASK_POSITIONS: tuple[Pos, ...] = tuple(TASK_NAMES)

@dataclass
class TaskState:
    assigned: list[Pos] = field(default_factory=list)
    completed: set[Pos] = field(default_factory=set)
    active: Optional[Pos] = None
    progress: float = 0.0


class TaskSystem:
    def __init__(self, game: "Game") -> None:
        self.states = {
            actor.id: TaskState(game.rng.sample(list(TASK_POSITIONS), game.config.tasks_per_player)
                                if actor.role == "crew" else [])
            for actor in game.players
        }

    def crew_progress(self, game: "Game") -> dict:
        """Shared progress for living crewmates, matching team task victory."""
        states = [self.states[actor.id] for actor in game.players if actor.alive and actor.role == "crew"]
        total = sum(len(state.assigned) for state in states)
        completed = sum(len(state.completed) for state in states)
        return {"completed": completed, "total": total, "remaining": total - completed,
                "fraction": completed / total if total else 0.0}

    def start(self, game: "Game", player_id: str) -> bool:
        actor = game.entity(player_id)
        state = self.states[player_id]
        available = [pos for pos in state.assigned if pos not in state.completed
                     and game.distance(actor.pos, pos) <= 1]
        if not actor.alive or not available or player_id in game.sabotage.workers:
            return False
        target = min(available, key=lambda pos: game.distance(actor.pos, pos))
        if state.active == target:
            return True
        state.active, state.progress = target, 0.0
        game.emit("task_started", f"{actor.name}: {TASK_NAMES[target]}", actor=player_id,
                  data={"position": list(target)}, visible_to=[player_id])
        return True

    def cancel(self, game: "Game", player_id: str, reason: str = "") -> None:
        state = self.states[player_id]
        active = state.active
        state.active, state.progress = None, 0.0
        if active is not None:
            game.emit("task_cancelled", reason or "Task cancelled.", actor=player_id,
                      visible_to=[player_id])

    def tick(self, game: "Game", dt: float) -> None:
        for actor in game.players:
            state = self.states[actor.id]
            if state.active is None:
                continue
            if not actor.alive or game.distance(actor.pos, state.active) > 1:
                self.cancel(game, actor.id, "Task interrupted.")
                continue
            state.progress = min(1.0, state.progress + dt / game.config.task_seconds)
            if state.progress >= 1.0 - 1e-9:
                finished = state.active
                state.completed.add(finished)
                state.active, state.progress = None, 0.0
                game.emit("task_completed", f"{actor.name} completed {TASK_NAMES[finished]}.",
                          actor=actor.id, data={"position": list(finished)}, visible_to=[actor.id])
        if self.victory_ready(game):
            game.finish("crew", "All required tasks have been completed.")

    def victory_ready(self, game: "Game") -> bool:
        if game.config.task_win_mode == "disabled" or not game.config.tasks_per_player:
            return False
        if game.config.task_win_mode == "player" and game.player.role == "crew":
            actors = [game.player]
        else:
            actors = [actor for actor in game.players if actor.alive and actor.role == "crew"]
        return bool(actors) and all(self.states[a.id].assigned
                                   and len(self.states[a.id].completed) == len(self.states[a.id].assigned)
                                   for a in actors)
