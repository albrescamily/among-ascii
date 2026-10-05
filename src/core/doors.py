"""Impostor door sabotage: lock a room's doorways for a few seconds."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from .world import DOORS, Door

if TYPE_CHECKING:
    from .engine import Game

# The Skeld's lockable rooms, in the order the mini map numbers them (keys 1-7).
DOOR_ROOMS = {
    "cafeteria": "Cafeteria", "storage": "Storage", "electrical": "Electrical", "medbay": "MedBay",
    "security": "Security", "upper_engine": "Upper Engine", "lower_engine": "Lower Engine",
}
DOOR_KEYS = {str(index): room_id for index, room_id in enumerate(DOOR_ROOMS, start=1)}
OPEN, CLOSED = "D", "C"  # Grid codes: closed doorways block movement and sight.


def room_doors(room_id: str) -> tuple[Door, ...]:
    return tuple(door for door in DOORS if door.room == DOOR_ROOMS.get(room_id))


class DoorSystem:
    def __init__(self) -> None:
        self.closed: dict[str, float] = {}    # room id -> seconds until it reopens
        self.cooldowns: dict[str, float] = {}  # room id -> seconds until it can close again

    @property
    def closed_count(self) -> int:
        """Rooms currently locked; the limit counts rooms, whatever their door count."""
        return len(self.closed)

    def refusal(self, game: "Game", actor_id: str, room_id: Optional[str]) -> Optional[str]:
        """Why this room cannot be locked right now, or None if it can."""
        actor = game.entity(actor_id)
        if room_id not in DOOR_ROOMS:
            return "Unknown room."
        if game.outcome or game.pending_meeting or not actor.alive or actor.role != "impostor":
            return "Only a living impostor can close doors."
        if game.sabotage.kind:
            return "Doors cannot be closed during a sabotage."
        if room_id in self.closed:
            return f"{DOOR_ROOMS[room_id]} doors are already closed."
        if self.cooldowns.get(room_id, 0.0) > 1e-9:
            return f"{DOOR_ROOMS[room_id]} doors recharge in {self.cooldowns[room_id]:.0f}s."
        limit = game.config.max_closed_rooms
        if self.closed_count >= limit:
            return f"At most {limit} rooms closed at once."
        return None

    def close(self, game: "Game", actor_id: str, room_id: Optional[str]) -> bool:
        if self.refusal(game, actor_id, room_id) is not None:
            return False
        for door in room_doors(room_id):
            for x, y in door.cells:
                game.grid[y][x] = CLOSED
        self.closed[room_id] = game.config.door_close_seconds
        game.emit("doors_closed", f"{DOOR_ROOMS[room_id]} doors closed.", actor=actor_id,
                  data={"room": room_id, "seconds": game.config.door_close_seconds},
                  visible_to=[actor_id])
        return True

    def reopen(self, game: "Game", room_id: str) -> None:
        if self.closed.pop(room_id, None) is None:
            return
        for door in room_doors(room_id):
            for x, y in door.cells:
                game.grid[y][x] = OPEN
        self.cooldowns[room_id] = game.config.door_cooldown

    def open_all(self, game: "Game") -> None:
        """Sabotages and meetings never leave a doorway locked."""
        for room_id in list(self.closed):
            self.reopen(game, room_id)

    def tick(self, game: "Game", dt: float) -> None:
        for room_id in list(self.cooldowns):
            self.cooldowns[room_id] = max(0.0, self.cooldowns[room_id] - dt)
        for room_id in list(self.closed):
            self.closed[room_id] -= dt
            if self.closed[room_id] <= 1e-9:
                self.reopen(game, room_id)
