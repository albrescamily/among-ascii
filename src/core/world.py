"""The Skeld layout and spatial queries."""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from typing import Optional
from .tasks import TASK_POSITIONS

MAP_W = 97
MAP_H = 41

Pos = tuple[int, int]


@dataclass(frozen=True)
class Room:
    name: str
    label: str
    bounds: tuple[int, int, int, int]
    label_y: Optional[int] = None

    def contains(self, pos: Pos) -> bool:
        x, y = pos
        left, top, right, bottom = self.bounds
        return left <= x <= right and top <= y <= bottom


# Floor coordinates: the gaps between rooms are hull or connecting corridors.
# The Skeld's two loops meet at Cafeteria and Storage; side rooms are dead ends.
ROOMS = (
    Room("Upper Engine", "UPPER ENGINE", (9, 5, 22, 11), label_y=5),
    Room("Cafeteria", "CAFETERIA", (38, 2, 60, 11), label_y=4),
    Room("Weapons", "WEAPONS", (72, 5, 85, 11)),
    Room("MedBay", "MEDBAY", (27, 14, 36, 21)),
    Room("Reactor", "REACTOR", (2, 16, 11, 26), label_y=16),
    Room("Security", "SECURITY", (17, 17, 25, 24)),
    Room("O2", "O2", (70, 16, 77, 21)),
    Room("Navigation", "NAV", (87, 16, 94, 27)),
    Room("Admin", "ADMIN", (55, 16, 66, 23)),
    Room("Electrical", "ELECTRICAL", (29, 26, 41, 31), label_y=27),
    Room("Storage", "STORAGE", (46, 27, 60, 39), label_y=28),
    Room("Lower Engine", "LOWER ENGINE", (9, 31, 22, 37), label_y=31),
    Room("Shields", "SHIELDS", (73, 31, 85, 37)),
    Room("Communications", "COMMS", (64, 37, 70, 39)),
)

# Inclusive exterior rectangles. Corridors stop at the room boundary instead
# of cutting through its corners; each entrance below spans both lanes.
CORRIDORS = (
    (23, 7, 37, 8), (31, 9, 32, 13),
    (13, 12, 14, 30), (12, 20, 12, 21), (15, 20, 16, 21),
    (23, 34, 45, 35), (34, 32, 35, 33),
    (49, 12, 50, 26), (51, 19, 54, 20),
    (61, 7, 71, 8), (81, 12, 82, 30),
    (78, 18, 80, 19), (83, 20, 86, 21),
    (61, 33, 72, 34), (68, 35, 69, 36),
)


@dataclass(frozen=True)
class Door:
    """An open doorway across a complete corridor, between two wall jambs."""

    room: str
    start: Pos
    axis: str
    width: int = 2

    @property
    def cells(self) -> tuple[Pos, ...]:
        x, y = self.start
        return tuple((x + offset, y) if self.axis == "horizontal" else (x, y + offset)
                     for offset in range(self.width))

    @property
    def glyph(self) -> str:
        return "┄" if self.axis == "horizontal" else "┆"


DOORS = (
    Door("Upper Engine", (23, 7), "vertical"),
    Door("Upper Engine", (13, 12), "horizontal"),
    Door("Cafeteria", (37, 7), "vertical"),
    Door("Cafeteria", (61, 7), "vertical"),
    Door("Cafeteria", (49, 12), "horizontal"),
    Door("Weapons", (71, 7), "vertical"),
    Door("Weapons", (81, 12), "horizontal"),
    Door("MedBay", (31, 13), "horizontal"),
    Door("Reactor", (12, 20), "vertical"),
    Door("Security", (16, 20), "vertical"),
    Door("Admin", (54, 19), "vertical"),
    Door("O2", (78, 18), "vertical"),
    Door("Navigation", (86, 20), "vertical"),
    Door("Electrical", (34, 32), "horizontal"),
    Door("Lower Engine", (13, 30), "horizontal"),
    Door("Lower Engine", (23, 34), "vertical"),
    Door("Storage", (49, 26), "horizontal"),
    Door("Storage", (45, 34), "vertical"),
    Door("Storage", (61, 33), "vertical"),
    Door("Shields", (81, 30), "horizontal"),
    Door("Shields", (72, 33), "vertical"),
    Door("Communications", (68, 36), "horizontal"),
)
DOOR_CELLS = {pos: door for door in DOORS for pos in door.cells}

EMERGENCY_POS: Pos = (49, 7)
SPAWNS: tuple[Pos, ...] = (
    (49, 10), (47, 7), (51, 7), (47, 10), (51, 10), (41, 7),
    (57, 7), (43, 8), (55, 8), (48, 5), (50, 5), (49, 11),
)
VENT_POSITIONS: dict[str, Pos] = {
    "upper_engine": (21, 7), "cafeteria": (56, 10), "weapons": (80, 5),
    "medbay": (28, 20), "reactor_lower": (3, 24), "reactor_upper": (10, 18),
    "security": (24, 23), "admin": (65, 22), "electrical": (29, 26),
    "lower_engine": (21, 33), "shields": (74, 36),
    "navigation_upper": (93, 18), "navigation_lower": (88, 25),
}
VENT_NETWORKS = (
    ("upper_engine", "reactor_upper"), ("lower_engine", "reactor_lower"),
    ("cafeteria", "admin"), ("weapons", "navigation_upper"),
    ("shields", "navigation_lower"), ("medbay", "security", "electrical"),
)
VENT_LINKS = {vent: tuple(other for other in network if other != vent)
              for network in VENT_NETWORKS for vent in network}
VENT_IDS = {pos: vent for vent, pos in VENT_POSITIONS.items()}
VENTS: set[Pos] = set(VENT_IDS)
# Inclusive solid rectangles. Engine task stations stay just above the cargo;
# the wall-mounted containers leave a route around their right-hand side.
CONTAINERS: dict[str, tuple[int, int, int, int]] = {
    "Upper Engine": (9, 8, 16, 9),
    "Lower Engine": (9, 34, 15, 35),
    "Electrical": (29, 28, 35, 29),
    "Storage": (50, 31, 56, 35),
    "Reactor": (2, 20, 7, 22),
}
FURNITURE: set[Pos] = {
    (x, y)
    for left, top, right, bottom in CONTAINERS.values()
    for y in range(top, bottom + 1)
    for x in range(left, right + 1)
}
ROOM_LABELS: dict[Pos, str] = {}
for _room in ROOMS:
    _left, _top, _right, _bottom = _room.bounds
    _start = (_left + _right - len(_room.label) + 1) // 2
    _label_y = _room.label_y if _room.label_y is not None else (_top + _bottom) // 2
    for _offset, _letter in enumerate(_room.label):
        ROOM_LABELS[(_start + _offset, _label_y)] = _letter


def build_map() -> list[list[str]]:
    """Expanded tile adaptation of The Skeld, with solid hull and 14 rooms."""
    grid = [[" " for _ in range(MAP_W)] for _ in range(MAP_H)]
    for y in range(MAP_H):
        for x in range(MAP_W):
            if any(room.contains((x, y)) for room in ROOMS):
                grid[y][x] = "."
    for left, top, right, bottom in CORRIDORS:
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                grid[y][x] = "."

    # Only outline the floor; the rest stays empty space outside the ship.
    floor = {(x, y) for y in range(MAP_H) for x in range(MAP_W) if grid[y][x] == "."}
    for x, y in floor:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < MAP_W and 0 <= ny < MAP_H and (nx, ny) not in floor:
                    grid[ny][nx] = "#"
    for x, y in DOOR_CELLS:
        grid[y][x] = "D"
    for x, y in FURNITURE:
        grid[y][x] = "O"

    for x, y in TASK_POSITIONS:
        grid[y][x] = "T"
    ex, ey = EMERGENCY_POS
    grid[ey][ex] = "E"
    for x, y in VENTS:
        grid[y][x] = "V"
    return grid


def manhattan(a: Pos, b: Pos) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def room_at(pos: Pos) -> str:
    return next((room.name for room in ROOMS if room.contains(pos)), "Hallway")



class ShipMap:
    """Static geometry, collision, pathfinding, and visibility."""

    def __init__(self) -> None:
        self.grid = build_map()

    def is_walkable(self, pos: Pos) -> bool:
        x, y = pos
        return 0 <= x < MAP_W and 0 <= y < MAP_H and self.grid[y][x] in ".DTEV"  # "C" (closed door) is not walkable

    def find_path(self, start: Pos, goal: Pos, *, use_vents: bool = False) -> list[Pos]:
        if not self.is_walkable(start) or not self.is_walkable(goal):
            return []
        if start == goal:
            return [start]
        queue: deque[Pos] = deque([start])
        came_from: dict[Pos, Optional[Pos]] = {start: None}
        while queue:
            current = queue.popleft()
            if current == goal:
                break
            x, y = current
            neighbors = [(x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)]
            if use_vents and current in VENT_IDS:
                neighbors.extend(VENT_POSITIONS[key] for key in VENT_LINKS[VENT_IDS[current]])
            for nxt in neighbors:
                if nxt not in came_from and self.is_walkable(nxt):
                    came_from[nxt] = current
                    queue.append(nxt)
        if goal not in came_from:
            return []
        path: list[Pos] = []
        current: Optional[Pos] = goal
        while current is not None:
            path.append(current)
            current = came_from[current]
        path.reverse()
        return path

    def has_line_of_sight(self, start: Pos, end: Pos, radius: int = 7) -> bool:
        if (start[0] - end[0]) ** 2 + (start[1] - end[1]) ** 2 > radius ** 2:
            return False
        x0, y0 = start
        x1, y1 = end
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            if (x0, y0) != start and (x0, y0) != end and self.grid[y0][x0] in "# OC":
                return False
            if (x0, y0) == (x1, y1):
                return True
            twice = 2 * error
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy
