"""Solid cargo, sight obstruction and relocated Electrical interactions."""
import unittest

from src import Game, GameConfig
from src.core.tasks import TASK_NAMES
from src.core.world import CONTAINERS, FURNITURE, VENT_POSITIONS
from src.terminal.palette import BLUE, GRAY, YELLOW
from src.terminal.text import ANSI_SGR, display_width
from src.terminal.ui import expand_map_cell, map_cell, observer_map_cell


class ContainerTests(unittest.TestCase):
    def test_containers_block_movement_sight_and_force_paths_around(self):
        game = Game(config=GameConfig(npc_ai_enabled=False))
        for room, (left, top, right, bottom) in CONTAINERS.items():
            with self.subTest(room=room):
                x = (left + right) // 2
                start, end = (x, top - 1), (x, bottom + 1)
                game.player_pos = start
                self.assertFalse(game.move_player(0, 1))
                self.assertEqual(game.player_pos, start)
                self.assertFalse(game.has_line_of_sight(start, end, radius=20))
                self.assertFalse(game.has_line_of_sight(end, start, radius=20))
                self.assertFalse(game.can_see(game.player_id, end))
                self.assertTrue(game.has_line_of_sight(start, (x, top), radius=20))
                path = game.find_path(start, end)
                self.assertEqual((path[0], path[-1]), (start, end))
                self.assertGreater(len(path) - 1, game.distance(start, end))
                self.assertFalse(set(path) & FURNITURE)

    def test_containers_render_hollow_but_stay_solid_in_both_map_views(self):
        game = Game()
        game.discovered.update(FURNITURE)
        for pos in FURNITURE:
            self.assertEqual(game.grid[pos[1]][pos[0]], "O")
            self.assertFalse(game.is_walkable(pos))
            for zoom in (1, 2):
                live = map_cell(game, pos, {pos}, zoom=zoom)
                observer = observer_map_cell(game, pos, zoom=zoom)
                # Same shape in both views; the God view paints the ship yellow.
                self.assertEqual(ANSI_SGR.sub("", live), ANSI_SGR.sub("", observer))
                self.assertIn(BLUE, live)
                self.assertIn(YELLOW, observer)
                self.assertEqual(display_width(expand_map_cell(live, zoom)), zoom)
                self.assertEqual(ANSI_SGR.sub("", map_cell(game, pos, set(), zoom=zoom)),
                                 ANSI_SGR.sub("", live))
        for left, top, right, bottom in CONTAINERS.values():
            for zoom in (1, 2):
                rows = ["".join(ANSI_SGR.sub("", observer_map_cell(game, (x, y), zoom=zoom))
                                for x in range(left, right + 1))
                        for y in range(top, bottom + 1)]
                width = (right - left + 1) * zoom
                self.assertEqual(rows[0], "#" * width)
                self.assertEqual(rows[-1], "#" * width)
                for row in rows[1:-1]:
                    self.assertEqual(row, "#" * zoom + " " * (width - 2 * zoom) + "#" * zoom)
                self.assertTrue(all(display_width(row) == width for row in rows))
        game.discovered.clear()
        for pos in FURNITURE:
            cell = map_cell(game, pos, set(), zoom=2)
            self.assertIn(GRAY, cell)
            self.assertEqual(ANSI_SGR.sub("", cell),
                             ANSI_SGR.sub("", observer_map_cell(game, pos, zoom=2)))
        self.assertFalse(any(38 <= x <= 60 and 2 <= y <= 11 for x, y in FURNITURE))

    def test_electrical_task_moves_to_yellow_mark_and_can_be_completed(self):
        game = Game(config=GameConfig(player_count=1, impostor_count=0,
                                      tasks_per_player=24, npc_ai_enabled=False))
        self.assertEqual(TASK_NAMES[(34, 26)], "Calibrate distributor")
        self.assertNotIn((30, 27), TASK_NAMES)
        game.player_pos = (34, 26)
        self.assertTrue(game.interact())
        game.tick(game.config.task_seconds)
        self.assertIn((34, 26), game.completed_tasks)

    def test_electrical_vent_moves_to_pink_mark_and_keeps_connections(self):
        game = Game(config=GameConfig(player_role="impostor", npc_ai_enabled=False))
        self.assertEqual(VENT_POSITIONS["electrical"], (29, 26))
        self.assertEqual(game.grid[30][30], ".")
        game.player_pos = VENT_POSITIONS["electrical"]
        self.assertTrue(game.apply_action(game.player_id, "vent"))
        for target in ("security", "electrical"):
            game.player.move_clock = 0
            self.assertTrue(game.apply_action(game.player_id, {"kind": "vent", "target": target}))
        game.player.move_clock = 0
        self.assertTrue(game.apply_action(game.player_id, "vent"))
        self.assertEqual(game.player_pos, (29, 26))
        self.assertIsNone(game.player.vent_id)


if __name__ == "__main__":
    unittest.main()
