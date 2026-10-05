import unittest
import io
import re
from collections import deque
from unittest.mock import patch

from src import Game, GameConfig
from src.core.world import EMERGENCY_POS, FURNITURE, MAP_H, MAP_W, ROOMS, SPAWNS, VENTS, room_at
from src.core.tasks import TASK_POSITIONS
from src.terminal.driver import Terminal
from src.terminal.text import ANSI_SGR, display_width, fit_line
from src.terminal.runtime import play_one
from src.terminal.ui import (render_end, render_game, render_help, render_meeting,
                         render_observer, render_vote_result, screen_layout)


class GameTests(unittest.TestCase):
    def test_map_and_path_are_connected(self):
        game = Game(seed=1)
        visited = {game.player_pos}
        queue = deque(visited)
        while queue:
            x, y = queue.popleft()
            for pos in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if game.is_walkable(pos) and pos not in visited:
                    visited.add(pos)
                    queue.append(pos)
        floors = {(x, y) for y in range(MAP_H) for x in range(MAP_W)
                  if game.is_walkable((x, y))}
        self.assertEqual(visited, floors)
        for target in (*TASK_POSITIONS, *VENTS, EMERGENCY_POS):
            with self.subTest(target=target):
                path = game.find_path(game.player_pos, target)
                self.assertEqual(path[0], game.player_pos)
                self.assertEqual(path[-1], target)
                self.assertTrue(all(game.is_walkable(pos) for pos in path))
                self.assertTrue(all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1
                                    for a, b in zip(path, path[1:])))

    def test_wall_blocks_player(self):
        game = Game(seed=1)
        game.player_pos = (9, 7)
        self.assertFalse(game.move_player(-1, 0))
        self.assertEqual(game.player_pos, (9, 7))

    def test_skeld_rooms_and_solid_exterior(self):
        game = Game(seed=1)
        self.assertEqual(len(ROOMS), 14)
        expected = {
            (15, 8): "Upper Engine", (49, 7): "Cafeteria",
            (78, 8): "Weapons", (31, 17): "MedBay", (6, 21): "Reactor",
            (21, 20): "Security", (73, 18): "O2",
            (90, 21): "Navigation", (60, 19): "Admin",
            (35, 28): "Electrical", (53, 33): "Storage",
            (15, 34): "Lower Engine", (79, 34): "Shields",
            (67, 38): "Communications", (28, 8): "Hallway",
        }
        for pos, name in expected.items():
            self.assertEqual(room_at(pos), name)
        for pos in ((0, 0), (-1, 5), (MAP_W, 5), *FURNITURE):
            self.assertFalse(game.is_walkable(pos))
        self.assertEqual(game.find_path((0, 0), EMERGENCY_POS), [])
        self.assertEqual(game.find_path((0, 0), (0, 0)), [])

    def test_side_rooms_do_not_leak_into_neighboring_rooms(self):
        game = Game(seed=1)
        # MedBay/Security and Communications/Shields need a separating wall.
        for room_name in ("MedBay", "Security", "Communications"):
            room = next(room for room in ROOMS if room.name == room_name)
            for y in range(MAP_H):
                for x in range(MAP_W):
                    if not room.contains((x, y)) or not game.is_walkable((x, y)):
                        continue
                    for pos in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                        if game.is_walkable(pos):
                            self.assertIn(room_at(pos), (room_name, "Hallway"))

    def test_spawns_and_meeting_reset_stay_in_cafeteria(self):
        game = Game(seed=1)
        for reset in (False, True):
            if reset:
                game.player_pos = TASK_POSITIONS[0]
                game.reset_after_meeting()
            positions = [game.player_pos, *(npc.pos for npc in game.npcs)]
            self.assertEqual(len(set(positions)), 12)
            self.assertEqual(set(positions), set(SPAWNS))
            for pos in positions:
                self.assertTrue(game.is_walkable(pos))
                self.assertEqual(room_at(pos), "Cafeteria")

    def test_task_completes_in_real_time(self):
        game = Game(seed=2)
        task = game.assigned_tasks[0]
        game.player_pos = task
        game.interact()
        self.assertEqual(game.active_task, task)
        for _ in range(30):
            game.tick(game.config.task_seconds / 20)
            if game.outcome:
                break
        self.assertIn(task, game.completed_tasks)

    def test_emergency_meeting(self):
        game = Game(seed=3)
        game.player_pos = EMERGENCY_POS
        game.interact()
        self.assertIsNotNone(game.pending_meeting)
        self.assertFalse(game.emergency_available)

    def test_impostor_can_create_body(self):
        game = Game(seed=4)
        impostor = game.npc(game.impostor_id)
        victim = next(n for n in game.npcs if n.role == "crew")
        impostor.pos = (12, 8)
        victim.pos = (12, 7)
        game.kill_cooldown = 0
        game.try_impostor_kill(impostor)
        self.assertFalse(victim.alive)
        self.assertEqual(len(game.bodies), 1)

    def test_visibility_is_blocked_by_wall(self):
        game = Game(seed=5)
        self.assertFalse(game.has_line_of_sight((25, 18), (27, 18), radius=10))

    def test_observer_screen_exposes_complete_player_state(self):
        game = Game(seed=6)
        screen = render_observer(game, (MAP_W + 36, MAP_H + 8))
        self.assertIn("FULL MAP", screen)
        self.assertIn("IMPOSTOR", screen)
        self.assertIn("Cyan", screen)
        for npc in game.npcs:
            self.assertIn(npc.name, screen)


class RenderingTests(unittest.TestCase):
    def test_universal_task_bar_is_visible_with_or_without_panel(self):
        game = Game(seed=6)
        actor = next(actor for actor in game.npcs if actor.role == "crew")
        game.tasks.states[actor.id].completed.add(game.tasks.states[actor.id].assigned[0])
        for size in ((60, 20), (133, 49), (230, 49)):
            for panel in (False, True):
                game.panel_visible = panel
                for renderer in (render_game, render_observer):
                    screen = ANSI_SGR.sub("", renderer(game, size))
                    row = screen.splitlines()[2]
                    self.assertIn("CREW TASKS 1/55", row)
                    self.assertIn("2%", row)
                    self.assertEqual(display_width(row), size[0] - 1)

    def test_voting_and_final_screens_remain_centered_after_resize(self):
        game = Game(seed=6)
        game.outcome = "victory"
        game.outcome_reason = "All required tasks have been completed."
        counts = {actor.id: 1 for actor in game.players}
        for size in ((60, 20), (80, 24), (120, 38), (230, 48)):
            with self.subTest(size=size), patch("src.terminal.text.shutil.get_terminal_size", return_value=size):
                screens = [render_meeting(game, game.player_id, None),
                           render_vote_result(game, None, counts), render_end(game)]
                for screen in screens:
                    lines = ANSI_SGR.sub("", screen).splitlines()
                    content = [(index, line) for index, line in enumerate(lines) if line.strip()]
                    top, bottom = content[0][0], content[-1][0]
                    left = min(len(line) - len(line.lstrip()) for _, line in content)
                    right = max(display_width(line.rstrip()) for _, line in content)
                    self.assertLessEqual(abs(top - (size[1] - 2 - bottom)), 1)
                    self.assertLessEqual(abs(left - (size[0] - 1 - right)), 1)
                for actor in game.players:
                    self.assertIn(actor.name, screens[0])
                self.assertIn("[0]", screens[0])
                self.assertIn("Press any key", screens[1])
                self.assertIn("play again", screens[2])

    def test_all_screens_fit_on_resize(self):
        game = Game(seed=6)
        game.messages.extend(["Long event " * 30] * 4)
        game.outcome_reason = "Long reason " * 30
        for size in ((230, 48), (133, 48), (180, 48), (166, 34), (120, 40), (101, 34), (98, 31),
                     (80, 24), (78, 22), (60, 20), (30, 10), (1, 1)):
            with self.subTest(size=size), patch("src.terminal.text.shutil.get_terminal_size", return_value=size):
                for active_task in (None, game.assigned_tasks[0]):
                    game.active_task = active_task
                    screens = [render_game(game), render_observer(game), render_help(game),
                               render_meeting(game, game.player_id, None, "Message " * 40),
                               render_vote_result(game, None, {None: 5}), render_end(game)]
                    for screen in screens:
                        self.assertLessEqual(len(screen.splitlines()), max(1, size[1] - 1))
                        for line in screen.splitlines():
                            self.assertLessEqual(display_width(line), size[0] - 1)
                        # There must be no partially sliced escape sequences.
                        self.assertNotIn("\x1b", ANSI_SGR.sub("", screen))

    def test_clipping_counts_cells_and_preserves_colors(self):
        text = "\x1b[31mA界e\u0301ZZ\x1b[0m"
        clipped = fit_line(text, 4)
        self.assertEqual(ANSI_SGR.sub("", clipped), "A界e\u0301")
        self.assertEqual(display_width(clipped), 4)
        self.assertEqual(display_width(fit_line(text, 10, pad=True)), 10)

    def test_camera_keeps_player_visible_at_ship_extremes(self):
        game = Game(seed=6)
        for pos in (SPAWNS[0], TASK_POSITIONS[8], TASK_POSITIONS[13], TASK_POSITIONS[-1]):
            game.player_pos = pos
            for zoom in (1, 2):
                game.map_zoom = zoom
                for panel_visible in (True, False):
                    game.panel_visible = panel_visible
                    screen = ANSI_SGR.sub("", render_game(game, (60, 20)))
                    self.assertIn("CAMERA", screen)
                    map_rows = [line.split("│")[1] for line in screen.splitlines()[4:-3]]
                    self.assertEqual("".join(map_rows).count("@"), 1)

    def test_map_frame_uses_available_space_at_both_zoom_levels(self):
        game = Game(seed=6)
        for size in ((60, 20), (101, 34), (120, 38), (133, 48), (230, 48)):
            for zoom in (None, 1, 2):
                game.map_zoom = zoom
                for panel_visible in (True, False):
                    game.panel_visible = panel_visible
                    for renderer in (render_game, render_observer):
                        with self.subTest(size=size, zoom=zoom, panel=panel_visible, view=renderer.__name__):
                            lines = ANSI_SGR.sub("", renderer(game, size)).splitlines()
                            self.assertEqual(len(lines), size[1] - 1)
                            for line in lines[3:-2]:
                                self.assertEqual(display_width(line), size[0] - 1)
                            for line in lines[4:-3]:
                                self.assertTrue(line.startswith("│") and line.endswith("│"))
                            self.assertTrue(lines[-3].startswith("└") and lines[-3].endswith("┘"))

    def test_large_monitor_shows_full_map_with_larger_tiles(self):
        game = Game(seed=6)
        screen = ANSI_SGR.sub("", render_observer(game, (MAP_W * 2 + 36, MAP_H + 8)))
        self.assertIn("2X FULL MAP", screen)
        self.assertIn("R E A C T O R", screen)
        self.assertIn("N A V", screen)
        self.assertIn("CREW MANIFEST", screen)
        game.panel_visible = False
        game.map_zoom = 1
        size = (MAP_W + 3, MAP_H + 8)
        self.assertEqual(screen_layout(game, size, observer=True).map_columns, MAP_W)
        self.assertIn("FULL MAP", render_observer(game, size))
        self.assertNotIn("CREW MANIFEST", render_observer(game, size))

    def test_zoom_and_panel_keys_change_the_live_game(self):
        game = Game(seed=6)
        terminal = unittest.mock.Mock()
        terminal.read_keys.side_effect = [["z"], ["p"], ["q"]]
        with patch("src.terminal.runtime.Game", return_value=game), patch("src.terminal.text.shutil.get_terminal_size", return_value=(120, 38)):
            result = play_one(terminal)
        self.assertIs(result, game)
        self.assertEqual(game.map_zoom, 1)
        self.assertFalse(game.panel_visible)

    def test_help_is_one_screen_and_observer_keeps_every_player(self):
        game = Game(seed=6)
        with patch("src.terminal.text.shutil.get_terminal_size", return_value=(60, 20)):
            help_screen = ANSI_SGR.sub("", render_help(game))
            self.assertIn("HOW TO PLAY", help_screen)
            self.assertNotIn("PROGRESS", help_screen)
            self.assertEqual(len({display_width(line) for line in help_screen.splitlines()}), 1)
            screens = []
            for page in range(2):
                game.observer_page = page
                screen = render_observer(game)
                screens.append(screen)
                self.assertIn("Kill cooldown", screen)
            for actor in game.players:
                self.assertIn(actor.name, "\n".join(screens))

    def test_draw_overwrites_shorter_screens_without_erasing_first(self):
        terminal = Terminal()
        output = io.StringIO()
        with patch("src.terminal.text.shutil.get_terminal_size", return_value=(60, 20)), patch("src.terminal.driver.sys.stdout", output):
            terminal.draw(("\x1b[31m" + "X" * 200 + "\x1b[0m\n") * 40)
            terminal.draw("short")
        stream = output.getvalue()
        self.assertNotIn("\n", stream)
        for erase in ("\x1b[2K", "\x1b[2J", "\x1b[J"):
            self.assertNotIn(erase, stream)
        updates = re.findall(r"\x1b\[(\d+);1H(.*?)(?=\x1b\[\d+;1H|$)", stream)
        surface = {}
        for row, contents in updates:
            self.assertLessEqual(int(row), 20)
            self.assertTrue(contents.endswith("\x1b[K"))
            printed = ANSI_SGR.sub("", contents[:-3])
            self.assertLessEqual(display_width(printed), 59)
            surface[int(row)] = printed
        self.assertEqual(surface[1], "short" + " " * 54)
        for row in range(2, 20):
            self.assertEqual(surface[row], " " * 59)
        self.assertEqual(surface[20], "")

    def test_identical_frames_do_not_write_or_flush(self):
        terminal = Terminal()
        output = unittest.mock.Mock()
        with patch("src.terminal.text.shutil.get_terminal_size", return_value=(60, 20)), patch("src.terminal.driver.sys.stdout", output):
            for _ in range(5):
                terminal.draw("STATIC MAP\nNPC A")
        output.write.assert_called_once()
        output.flush.assert_called_once()

    def test_only_changed_rows_are_sent_in_one_batch(self):
        terminal = Terminal()
        output = unittest.mock.Mock()
        with patch("src.terminal.text.shutil.get_terminal_size", return_value=(60, 20)), patch("src.terminal.driver.sys.stdout", output):
            terminal.draw("STATIC MAP\nNPC A\nUNCHANGED FOOTER")
            output.reset_mock()
            terminal.draw("STATIC MAP\nNPC B\nUNCHANGED FOOTER")
        output.write.assert_called_once()
        output.flush.assert_called_once()
        stream = output.write.call_args.args[0]
        self.assertEqual(re.findall(r"\x1b\[(\d+);1H", stream), ["2"])
        self.assertIn("NPC B", stream)
        self.assertNotIn("STATIC MAP", stream)
        self.assertNotIn("UNCHANGED FOOTER", stream)
        self.assertNotIn("\x1b[2K", stream)

    def test_color_changes_repaint_even_when_text_stays_the_same(self):
        terminal = Terminal()
        output = unittest.mock.Mock()
        with patch("src.terminal.text.shutil.get_terminal_size", return_value=(60, 20)), patch("src.terminal.driver.sys.stdout", output):
            terminal.draw("\x1b[31mV\x1b[0m")
            output.reset_mock()
            terminal.draw("\x1b[32mV\x1b[0m")
        output.write.assert_called_once()
        self.assertIn("\x1b[32mV", output.write.call_args.args[0])

    def test_resize_repaints_and_clears_reserved_edges(self):
        terminal = Terminal()
        output = unittest.mock.Mock()
        for size in ((80, 24), (60, 20), (100, 32)):
            with self.subTest(size=size), patch("src.terminal.text.shutil.get_terminal_size", return_value=size), patch("src.terminal.driver.sys.stdout", output):
                output.reset_mock()
                terminal.draw("SAME CONTENT")
                stream = output.write.call_args.args[0]
                updates = re.findall(r"\x1b\[(\d+);1H(.*?)(?=\x1b\[\d+;1H|$)", stream)
                self.assertEqual([int(row) for row, _ in updates], list(range(1, size[1] + 1)))
                for _, contents in updates[:-1]:
                    self.assertTrue(contents.endswith("\x1b[K"))
                    self.assertEqual(display_width(contents[:-3]), size[0] - 1)
                self.assertEqual(ANSI_SGR.sub("", updates[-1][1]), "\x1b[K")


if __name__ == "__main__":
    unittest.main()
