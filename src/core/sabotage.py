"""Shared critical-emergency rules for Game and Simulation modes."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
from .models import Pos

if TYPE_CHECKING:
    from .engine import Game


@dataclass(frozen=True)
class RepairPanel:
    name: str
    pos: Pos


PANELS = {
    "reactor": (RepairPanel("Reactor north", (5, 18)), RepairPanel("Reactor south", (6, 24))),
    "o2": (RepairPanel("O2", (75, 17)), RepairPanel("Admin oxygen", (64, 20))),
    "admin": (RepairPanel("Admin systems", (58, 17)),),
    "lights": (RepairPanel("Electrical lights", (39, 29)),),
}
TITLES = {"reactor": "REACTOR MELTDOWN", "o2": "OXYGEN DEPLETED", "admin": "ADMIN SYSTEM FAILURE",
          "lights": "LIGHTS OUT"}
SABOTAGE_KEYS = {"f": "reactor", "g": "o2", "j": "admin", "l": "lights"}
# Critical emergencies run a countdown, lock the emergency button and stop tasks.
CRITICAL = frozenset({"reactor", "o2", "admin"})


class SabotageSystem:
    def __init__(self, game: "Game") -> None:
        self.kind: str | None = None
        self.remaining = 0.0
        self.cooldown = game.config.initial_sabotage_cooldown
        self.workers: dict[str, int] = {}
        self.progress: dict[int, float] = {}
        self.completed: set[int] = set()
        self.responders: dict[str, int] = {}

    @property
    def critical(self) -> bool:
        return self.kind in CRITICAL

    @property
    def panels(self) -> tuple[RepairPanel, ...]:
        return PANELS.get(self.kind, ())

    def start(self, game: "Game", actor_id: str, kind: str) -> bool:
        actor = game.entity(actor_id)
        if (kind not in PANELS or not game.config.sabotage_enabled
                or self.kind or self.cooldown > 1e-9 or game.outcome or game.pending_meeting
                or not actor.alive or actor.role != "impostor"):
            return False
        self.kind = kind
        self.remaining = game.config.sabotage_seconds if self.critical else 0.0
        self.workers.clear()
        self.progress.clear()
        self.completed.clear()
        self.responders.clear()
        if not self.critical:
            game.emit("sabotage_started", f"{TITLES[kind]}! Crew vision is reduced until Electrical is fixed.",
                      data={"kind": kind, "seconds": None})
            return True
        for other in game.players:
            game.tasks.cancel(game, other.id)
            other.path, other.goal, other.rethink_clock = [], None, 0.0
        game.emit("sabotage_started", f"{TITLES[kind]}! Repair the ! panels before time runs out.",
                  data={"kind": kind, "seconds": self.remaining})
        return True

    def nearby_panel(self, game: "Game", actor_id: str) -> int | None:
        actor = game.entity(actor_id)
        if not actor.alive or actor.vent_id is not None:
            return None
        return next((i for i, panel in enumerate(self.panels)
                     if i not in self.completed and game.distance(actor.pos, panel.pos) <= 1), None)

    def interact(self, game: "Game", actor_id: str) -> bool:
        if game.outcome or game.pending_meeting:
            return False
        index = self.nearby_panel(game, actor_id)
        if index is None:
            return False
        game.tasks.cancel(game, actor_id)
        self.workers[actor_id] = index
        return True

    def cancel_worker(self, actor_id: str) -> None:
        index = self.workers.pop(actor_id, None)
        if index is not None and index not in self.workers.values():
            self.progress[index] = 0.0
            if self.kind == "reactor":
                self.progress.clear()

    def clear(self, game: "Game", *, reported: bool = False) -> None:
        if not self.kind:
            return
        kind = self.kind
        self.kind, self.remaining = None, 0.0
        self.cooldown = game.config.sabotage_cooldown
        self.workers.clear()
        self.completed.clear()
        self.progress.clear()
        self.responders.clear()
        for actor in game.players:
            actor.path, actor.goal, actor.rethink_clock = [], None, 0.0
        game.emit("sabotage_cancelled" if reported else "sabotage_repaired",
                  "Emergency ended by body report." if reported else f"{TITLES[kind]} resolved. Systems restored.",
                  data={"kind": kind})

    def npc_goal(self, game: "Game", actor_id: str) -> Pos | None:
        """Assign only the responders needed; reserve different stations."""
        for assigned, index in list(self.responders.items()):
            human_holding = self.workers.get(game.player_id) == index
            if not game.entity(assigned).alive or index in self.completed or human_holding:
                self.responders.pop(assigned)
                self.cancel_worker(assigned)
        taken = set(self.responders.values()) | set(self.workers.values()) | self.completed
        for index, panel in enumerate(self.panels):
            if index in taken:
                continue
            candidates = [a for a in game.npcs if a.alive and a.role == "crew"
                          and a.id not in self.responders and a.id not in self.workers]
            if candidates:
                actor = min(candidates, key=lambda a: len(game.find_path(a.pos, panel.pos)) or 10000)
                self.responders[actor.id] = index
                actor.path, actor.goal, actor.rethink_clock = [], None, 0.0
        index = self.responders.get(actor_id)
        return self.panels[index].pos if index is not None else None

    def tick(self, game: "Game", dt: float) -> None:
        if not self.kind:
            self.cooldown = max(0.0, self.cooldown - dt)
            return
        if self.critical:
            dt = min(dt, self.remaining)
        for actor_id, index in list(self.workers.items()):
            actor = game.entity(actor_id)
            if (index in self.completed or not actor.alive or actor.vent_id is not None
                    or game.distance(actor.pos, self.panels[index].pos) > 1):
                self.cancel_worker(actor_id)
        occupied = set(self.workers.values())
        simultaneous = self.kind == "reactor"
        ready = len(occupied) == len(self.panels)
        for index, panel in enumerate(self.panels):
            if index in self.completed:
                continue
            if index in occupied and (not simultaneous or ready):
                self.progress[index] = min(1.0, self.progress.get(index, 0.0)
                                           + dt / game.config.sabotage_repair_seconds)
                if self.progress[index] >= 1.0 - 1e-9:
                    self.completed.add(index)
                    game.emit("sabotage_panel_repaired", f"{panel.name}: repair complete.",
                              data={"kind": self.kind, "panel": index})
            else:
                self.progress[index] = 0.0
        if len(self.completed) == len(self.panels):
            self.clear(game)
            return
        if not self.critical:
            return
        self.remaining = max(0.0, self.remaining - dt)
        if self.remaining <= 1e-9:
            game.finish("impostor", f"{TITLES[self.kind]}: the crew ran out of time.")
