"""Impostor vent traversal and visibility of entry/exit events."""
from __future__ import annotations
from typing import TYPE_CHECKING
from .world import VENT_LINKS, VENT_POSITIONS, manhattan, room_at

if TYPE_CHECKING:
    from .engine import Game


class VentSystem:
    def nearby(self, game: "Game", player_id: str) -> str | None:
        actor = game.entity(player_id)
        candidates = [key for key, pos in VENT_POSITIONS.items()
                      if manhattan(actor.pos, pos) <= 1
                      and game.has_line_of_sight(actor.pos, pos, 1)]
        return min(candidates, key=lambda key: (manhattan(actor.pos, VENT_POSITIONS[key]), key),
                   default=None)

    def use(self, game: "Game", player_id: str, target: str | None = None) -> bool:
        actor = game.entity(player_id)
        if (not actor.alive or actor.role != "impostor" or game.outcome
                or game.pending_meeting or actor.move_clock > 1e-9):
            return False
        if actor.vent_id is None:
            source = self.nearby(game, player_id)
            if source is None or target is not None:
                return False
            actor.pos = VENT_POSITIONS[source]
            actor.vent_id = source
            game.sabotage.cancel_worker(player_id)
            game.tasks.cancel(game, player_id)
            self.emit_surface_event(game, player_id, "vent_entered", source)
        elif target is not None:
            if target not in VENT_LINKS[actor.vent_id]:
                return False
            source = actor.vent_id
            actor.vent_id = target
            actor.pos = VENT_POSITIONS[target]
            game.emit("vent_traveled", f"Vent: {room_at(actor.pos)}.", actor=player_id,
                      data={"from": source, "vent": target, "position": list(actor.pos)},
                      visible_to=[player_id])
        else:
            if game.occupied(actor.pos, player_id):
                return False
            source = actor.vent_id
            actor.vent_id = None
            self.emit_surface_event(game, player_id, "vent_exited", source)
        actor.move_clock = game.move_interval(actor)
        return True

    def emit_surface_event(self, game: "Game", player_id: str, kind: str, vent: str) -> None:
        actor = game.entity(player_id)
        witnesses = [other for other in game.players
                     if other.id != player_id and game.can_see(other.id, actor.pos)]
        for other in witnesses:
            other.suspicion[player_id] = other.suspicion.get(player_id, 0.0) + 8.0
        verb = "entered" if kind == "vent_entered" else "exited"
        game.emit(kind, f"{actor.name} {verb} a vent in {room_at(actor.pos)}.", actor=player_id,
                  data={"vent": vent, "position": list(actor.pos)},
                  visible_to=[player_id, *(other.id for other in witnesses)])

    def observe(self, game: "Game", player_id: str) -> dict:
        actor = game.entity(player_id)
        nearby = self.nearby(game, player_id) if actor.alive and actor.role == "impostor" else None
        return {"current": actor.vent_id, "nearby": nearby,
                "connections": [{"id": key, "room": room_at(VENT_POSITIONS[key]),
                                 "position": list(VENT_POSITIONS[key])}
                                for key in VENT_LINKS.get(actor.vent_id, ())]}
