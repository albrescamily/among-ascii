"""Regression coverage for room boundaries and full-width open doorways."""

import unittest
from collections import deque

from src import Game
from src.core.world import DOORS, DOOR_CELLS, EMERGENCY_POS, FURNITURE, MAP_H, MAP_W, ROOMS, ROOM_LABELS, VENTS
from src.core.tasks import TASK_POSITIONS
from src.terminal.text import ANSI_SGR, display_width
from src.terminal.ui import map_cell, observer_map_cell


class MapGeometryTests(unittest.TestCase):
    def test_doorways_fill_the_passage_between_wall_jambs(self):
        game = Game(seed=1)
        rooms = {room.name: room for room in ROOMS}
        actual_doors = {(x, y) for y in range(MAP_H) for x in range(MAP_W)
                        if game.grid[y][x] == "D"}
        self.assertEqual(actual_doors, set(DOOR_CELLS))
        self.assertEqual(sum(len(door.cells) for door in DOORS), len(actual_doors))
        self.assertEqual({door.room for door in DOORS}, set(rooms))

        for door in DOORS:
            with self.subTest(room=door.room, start=door.start):
                self.assertEqual(len(door.cells), 2)
                self.assertIn(door.axis, ("horizontal", "vertical"))
                along = (1, 0) if door.axis == "horizontal" else (0, 1)
                across = (0, 1) if door.axis == "horizontal" else (1, 0)
                first, last = door.cells[0], door.cells[-1]
                jambs = ((first[0] - along[0], first[1] - along[1]),
                         (last[0] + along[0], last[1] + along[1]))
                for x, y in jambs:
                    self.assertEqual(game.grid[y][x], "#", (door.room, (x, y)))
                for x, y in door.cells:
                    self.assertIs(DOOR_CELLS[(x, y)], door)
                    sides = ((x - across[0], y - across[1]),
                             (x + across[0], y + across[1]))
                    self.assertTrue(all(game.is_walkable(pos) for pos in sides))
                    self.assertEqual(sum(rooms[door.room].contains(pos)
                                         for pos in sides), 1)

    def test_sealed_thresholds_leave_no_side_room_or_corridor_bypass(self):
        game = Game(seed=1)
        for x, y in DOOR_CELLS:
            game.grid[y][x] = "#"

        for room in ROOMS:
            with self.subTest(room=room.name):
                room_floor = {(x, y) for y in range(MAP_H) for x in range(MAP_W)
                              if room.contains((x, y)) and game.is_walkable((x, y))}
                self.assertTrue(room_floor)
                seen = {next(iter(room_floor))}
                queue = deque(seen)
                while queue:
                    x, y = queue.popleft()
                    for pos in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                        if game.is_walkable(pos) and pos not in seen:
                            seen.add(pos)
                            queue.append(pos)
                self.assertEqual(seen, room_floor,
                                 f"{room.name} leaks through a wall or has disconnected floor")

    def test_reactor_and_navigation_have_one_central_entrance(self):
        game = Game(seed=1)
        for room_name in ("Reactor", "Navigation"):
            with self.subTest(room=room_name):
                entrances = [door for door in DOORS if door.room == room_name]
                self.assertEqual(len(entrances), 1)
                self.assertEqual(entrances[0].axis, "vertical")
                room = next(room for room in ROOMS if room.name == room_name)
                left, top, right, bottom = room.bounds
                x = right + 1 if room_name == "Reactor" else left - 1
                self.assertTrue(all(px == x for px, _ in entrances[0].cells))
                entrance_center = sum(y for _, y in entrances[0].cells) / 2
                self.assertLessEqual(abs(entrance_center - (top + bottom) / 2), 1)
                for y in range(top, bottom + 1):
                    if (x, y) not in entrances[0].cells:
                        self.assertEqual(game.grid[y][x], "#")

                # Inspect every room edge, so an unmarked second passage
                # cannot silently survive after its door sprite is removed.
                exits = set()
                for ry in range(MAP_H):
                    for rx in range(MAP_W):
                        if not room.contains((rx, ry)) or not game.is_walkable((rx, ry)):
                            continue
                        for pos in ((rx - 1, ry), (rx + 1, ry), (rx, ry - 1), (rx, ry + 1)):
                            if game.is_walkable(pos) and not room.contains(pos):
                                exits.add(pos)
                self.assertEqual(exits, set(entrances[0].cells))

    def test_player_and_npc_cross_every_open_doorway(self):
        for door in DOORS:
            across = (0, 1) if door.axis == "horizontal" else (1, 0)
            for x, y in door.cells:
                for direction in (-1, 1):
                    with self.subTest(room=door.room, cell=(x, y), direction=direction):
                        game = Game(seed=1)
                        for npc in game.npcs:
                            npc.alive = False
                        dx, dy = across[0] * direction, across[1] * direction
                        start, end = (x - dx, y - dy), (x + dx, y + dy)
                        game.player_pos = start
                        self.assertTrue(game.move_player(dx, dy))
                        self.assertEqual(game.player_pos, (x, y))
                        self.assertTrue(game.move_player(dx, dy))
                        self.assertEqual(game.player_pos, end)

                        game.player_alive = False
                        npc = game.npcs[0]
                        npc.alive = True
                        npc.pos, npc.goal = start, end
                        npc.path = game.find_path(start, end)
                        npc.rethink_clock = 10.0
                        game.move_npc(npc)
                        self.assertEqual(npc.pos, (x, y))
                        game.move_npc(npc)
                        self.assertEqual(npc.pos, end)

    def test_room_labels_do_not_cover_tasks_vents_furniture_or_doors(self):
        game = Game(seed=1)
        reserved = set(TASK_POSITIONS) | VENTS | FURNITURE | {EMERGENCY_POS} | set(DOOR_CELLS)
        self.assertFalse(set(ROOM_LABELS) & reserved)
        for pos in ROOM_LABELS:
            with self.subTest(pos=pos):
                self.assertEqual(game.grid[pos[1]][pos[0]], ".")
                self.assertTrue(any(room.contains(pos) for room in ROOMS))

    def test_expanded_map_keeps_all_landmarks_inside_distinct_rooms(self):
        game = Game(seed=1)
        self.assertEqual((MAP_W, MAP_H), (97, 41))
        self.assertEqual((len(game.grid[0]), len(game.grid)), (MAP_W, MAP_H))
        floor_count = sum(game.is_walkable((x, y)) for y in range(MAP_H) for x in range(MAP_W))
        self.assertGreater(floor_count, 1500)
        groups = (set(TASK_POSITIONS), VENTS, FURNITURE, {EMERGENCY_POS})
        for index, group in enumerate(groups):
            for other in groups[index + 1:]:
                self.assertFalse(group & other)
            for pos in group:
                self.assertEqual(sum(room.contains(pos) for room in ROOMS), 1, pos)
        self.assertFalse(set(ROOM_LABELS) & {actor.pos for actor in game.players})

    def test_door_direction_stays_visible_in_live_observer_and_explored_views(self):
        game = Game(seed=1)
        game.discovered.update(DOOR_CELLS)
        self.assertEqual({door.axis for door in DOORS}, {"horizontal", "vertical"})
        for pos, door in DOOR_CELLS.items():
            with self.subTest(room=door.room, pos=pos):
                for code, expected in (("D", "-"), ("C", "+")):  # open, closed
                    game.grid[pos[1]][pos[0]] = code
                    cells = (observer_map_cell(game, pos), map_cell(game, pos, {pos}),
                             map_cell(game, pos, set()))
                    for cell in cells:
                        self.assertEqual(ANSI_SGR.sub("", cell), expected)
                        self.assertEqual(display_width(cell), 1)
                game.grid[pos[1]][pos[0]] = "D"


if __name__ == "__main__":
    unittest.main()
