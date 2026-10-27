"""Keyboard bindings: which key asks for which game action."""
from __future__ import annotations
from ..core.systems.doors import DOOR_ROOMS

# Keys to move kinds; the offsets live in core.models.MOVE_ACTIONS.
MOVE_KEYS = {
    "w": "up", "up": "up",
    "s": "down", "down": "down",
    "a": "left", "left": "left",
    "d": "right", "right": "right",
}
SABOTAGE_KEYS = {"f": "reactor", "g": "o2", "j": "admin", "l": "lights"}
# Mini map number keys close a room's doors, in DOOR_ROOMS order (1-7).
DOOR_KEYS = {str(index): room_id for index, room_id in enumerate(DOOR_ROOMS, start=1)}
