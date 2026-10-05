"""Test map sandbox: idle NPCs, no round end, role swap and reset."""
import unittest
from unittest.mock import Mock, patch

from src import Action, Game, GameConfig
from src.core.sabotage import PANELS
from src.core.world import EMERGENCY_POS
from src.terminal.menu import adjust_setting, MENU_ITEMS
from src.terminal.runtime import play_one
from src.terminal.text import ANSI_SGR
from src.terminal.ui import render_game, render_help, render_observer


def make_game(**overrides):
    return Game(config=GameConfig(play_mode="test", player_role="crew", seed=3, **overrides))


def next_to(game, target):
    x, y = target.pos
    for pos in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
        if game.is_walkable(pos) and not game.occupied(pos):
            return pos
    raise AssertionError("no free tile")


class TestMapTests(unittest.TestCase):
    def test_npcs_are_idle_dummies_and_round_never_ends(self):
        game = make_game()
        positions = [a.pos for a in game.players]
        for _ in range(12):
            game.tick(60)
        self.assertIsNone(game.outcome)
        self.assertEqual(positions, [a.pos for a in game.players])
        self.assertEqual(len(game.npcs), game.config.player_count - 1)

    def test_swap_role_unlocks_impostor_and_back_to_crew(self):
        game = make_game()
        self.assertTrue(game.assigned_tasks)
        game.set_role(game.player_id, "impostor")
        self.assertEqual(game.player.role, "impostor")
        self.assertIn(game.player_id, game.impostor_ids)
        victim = next(a for a in game.npcs if a.role == "crew")
        game.player_pos = next_to(game, victim)
        self.assertTrue(game.kill(game.player_id))
        self.assertFalse(victim.alive)
        self.assertIsNone(game.outcome)
        game.tick(game.config.kill_cooldown)
        self.assertTrue(game.apply_action(game.player_id, Action("sabotage", "lights")))
        game.set_role(game.player_id, "crew")
        self.assertNotIn(game.player_id, game.impostor_ids)
        self.assertEqual(game.vision_radius(game.player_id), game.config.lights_vision_radius)
        self.assertTrue(game.assigned_tasks)

    def test_critical_timeout_and_parity_do_not_end_test(self):
        game = make_game()
        game.set_role(game.player_id, "impostor")
        self.assertTrue(game.apply_action(game.player_id, Action("sabotage", "reactor")))
        game.tick(game.config.sabotage_seconds + 1)
        self.assertIsNone(game.outcome)
        self.assertIsNone(game.sabotage.kind)
        for actor in game.npcs:
            actor.alive = False
        game.tick(1)
        self.assertIsNone(game.outcome)

    def test_reset_revives_and_clears(self):
        game = make_game()
        game.set_role(game.player_id, "impostor")
        victim = next(a for a in game.npcs if a.role == "crew")
        game.player_pos = next_to(game, victim)
        game.kill(game.player_id)
        game.apply_action(game.player_id, Action("sabotage", "o2"))
        game.reset_test()
        self.assertTrue(all(a.alive for a in game.players))
        self.assertFalse(game.bodies)
        self.assertIsNone(game.sabotage.kind)
        self.assertEqual(game.sabotage.cooldown, 0)
        self.assertEqual(game.kill_cooldown, 0)

    def test_crew_can_do_tasks_and_emergency(self):
        game = make_game()
        game.player_pos = next_to(game, Mock(pos=game.assigned_tasks[0]))
        self.assertTrue(game.interact())
        game.tick(game.config.task_seconds + 0.1)
        self.assertIn(game.assigned_tasks[0], game.completed_tasks)
        game.player_pos = EMERGENCY_POS
        self.assertTrue(game.call_emergency(game.player_id))


class TestMapTerminalTests(unittest.TestCase):
    def test_crew_panel_lists_finished_tasks_in_green(self):
        from src.core.tasks import TASK_NAMES
        from src.terminal.palette import GREEN
        game = make_game()
        done = game.assigned_tasks[0]
        game.completed_tasks.add(done)
        screen = render_game(game, (130, 42))
        self.assertIn("Do your tasks.", ANSI_SGR.sub("", screen))
        self.assertIn(GREEN + " ✓ " + TASK_NAMES[done], screen)

    def test_impostor_panel_replaces_task_info(self):
        game = make_game()
        crew_screen = ANSI_SGR.sub("", render_game(game, (120, 38)))
        self.assertIn("TASK PROGRESS", crew_screen)
        self.assertIn("ASSIGNMENTS", crew_screen)
        game.set_role(game.player_id, "impostor")
        screen = ANSI_SGR.sub("", render_game(game, (120, 38)))
        self.assertNotIn("TASK PROGRESS", screen)
        self.assertNotIn("ASSIGNMENTS", screen)
        self.assertIn("Kill: READY", screen)
        self.assertIn("Kill the crewmates.", screen)
        game.player_pos = next_to(game, Mock(pos=game.assigned_tasks[0]))
        self.assertFalse(game.interact())

    def test_menu_cycles_to_test_map(self):
        config = GameConfig()
        index = MENU_ITEMS.index("play_mode")
        config = adjust_setting(config, index, 1)
        self.assertEqual(config.play_mode, "simulation")
        config = adjust_setting(config, index, 1)
        self.assertEqual(config.play_mode, "test")
        self.assertEqual(adjust_setting(config, index, 1).play_mode, "game")

    def test_keys_swap_role_and_reset(self):
        game = make_game()
        term = Mock()
        term.read_keys.side_effect = [["x"], ["n"], ["q"]]
        with patch("src.terminal.runtime.Game", return_value=game), patch(
                "src.terminal.text.shutil.get_terminal_size", return_value=(120, 38)):
            play_one(term)
        self.assertEqual(game.player.role, "impostor")
        self.assertEqual(game.outcome, "stopped")
        plain = ANSI_SGR.sub("", render_game(game, (120, 38)))
        self.assertIn("PLAYER CYAN / IMPOSTOR", plain)
        self.assertIn("MODE TEST / PLAYER VIEW", plain)
        self.assertIn("BUTTONS 1", plain.splitlines()[1])
        god = ANSI_SGR.sub("", render_observer(game, (120, 38)))
        self.assertIn("MODE TEST / GOD VIEW", god)
        simulation = ANSI_SGR.sub("", render_observer(Game(config=GameConfig(play_mode="simulation")), (120, 38)))
        self.assertIn("MODE SIMULATION", simulation.splitlines()[0])
        self.assertNotIn("GOD VIEW", simulation.splitlines()[0])
        self.assertNotIn("BUTTONS", simulation.splitlines()[1])
        self.assertIn("TEST MAP", plain)
        self.assertNotIn("TASKS 0/", plain.splitlines()[1])
        game.set_role(game.player_id, "crew")
        header = ANSI_SGR.sub("", render_game(game, (120, 38))).splitlines()[1]
        self.assertIn(f"TASKS 0/{len(game.assigned_tasks)}", header)
        self.assertIn("swap role", ANSI_SGR.sub("", render_help(game)))


if __name__ == "__main__":
    unittest.main()
