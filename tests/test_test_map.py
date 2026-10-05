"""Test mode sandbox: idle NPCs, no round end, role swap and reset."""
import unittest
from unittest.mock import Mock, patch

from src import Action, Game, GameConfig
from src.core.sabotage import PANELS
from src.core.world import EMERGENCY_POS
from src.terminal.menu import adjust_setting, MENU_ITEMS, render_setup
from src.terminal.runtime import play_one
from src.terminal.text import ANSI_SGR
from src.terminal.ui import render_game, render_help, render_observer


def make_game(**overrides):
    return Game(config=GameConfig(play_mode="game", test_mode=True, player_role="crew", seed=3, **overrides))


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
    def test_minimap_shows_the_ship_and_you_but_hides_other_players(self):
        from src.core.models import Body
        from src.terminal.ui import minimap_cell, render_minimap
        game = make_game()
        npc = game.npcs[0]
        npc.pos = (49, 15)
        game.bodies.append(Body("x", "X", (50, 15), "Hallway", 0))
        for pos in (npc.pos, (50, 15)):
            self.assertNotIn(npc.symbol, ANSI_SGR.sub("", minimap_cell(game, pos)))
            self.assertNotIn("†", ANSI_SGR.sub("", minimap_cell(game, pos)))
        self.assertEqual(ANSI_SGR.sub("", minimap_cell(game, game.player_pos)), "@")
        from src.terminal.palette import YELLOW
        self.assertIn(YELLOW + "◆", minimap_cell(game, game.assigned_tasks[0]))
        screen = ANSI_SGR.sub("", render_minimap(game, (130, 42)))
        self.assertIn("MODE TEST / MINI MAP", screen)
        self.assertIn("LEGEND", screen)

    def view_screens(self, keys, caps):
        game = make_game()
        term = Mock()
        term.read_keys.side_effect = keys
        term.caps_lock.return_value = caps
        with patch("src.terminal.runtime.Game", return_value=game), patch(
                "src.terminal.text.shutil.get_terminal_size", return_value=(130, 42)), patch(
                "src.terminal.runtime.time.monotonic", side_effect=range(100)):
            play_one(term)
        return [ANSI_SGR.sub("", call.args[0]).splitlines()[0] for call in term.draw.call_args_list]

    def test_caps_lock_holds_god_view_and_tab_opens_the_minimap(self):
        self.assertTrue(all("GOD VIEW" in s for s in self.view_screens([[], [], ["q"]], True)))
        self.assertTrue(all("PLAYER VIEW" in s for s in self.view_screens([[], [], ["q"]], False)))
        self.assertIn("MINI MAP", self.view_screens([["\t"], [], ["q"]], True)[-1])
        # Without Caps Lock support, the ` key toggles the God view instead.
        self.assertIn("GOD VIEW", self.view_screens([["`"], [], ["q"]], None)[-1])

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

    def test_test_mode_and_god_view_are_game_mode_options(self):
        config = GameConfig()
        mode = MENU_ITEMS.index("play_mode")
        self.assertEqual(adjust_setting(config, mode, 1).play_mode, "simulation")
        self.assertEqual(adjust_setting(adjust_setting(config, mode, 1), mode, 1).play_mode, "game")
        god = MENU_ITEMS.index("allow_god_view")
        config = adjust_setting(config, god, 1)
        self.assertFalse(config.allow_god_view)
        self.assertFalse(Game(config=config).god_view_allowed)
        config = adjust_setting(config, MENU_ITEMS.index("test_mode"), 1)
        self.assertTrue(config.test_mode)
        self.assertTrue(Game(config=config).test_mode)
        # The Test mode forces the God view on, and the menu option is locked.
        self.assertTrue(Game(config=config).god_view_allowed)
        self.assertEqual(adjust_setting(config, god, 1), config)
        self.assertIn("On (test mode)", ANSI_SGR.sub("", render_setup(config, god)))
        # Simulation ignores both; the menu marks them as game-only.
        simulation = adjust_setting(config, mode, 1)
        self.assertFalse(Game(config=simulation).test_mode)
        self.assertEqual(adjust_setting(simulation, MENU_ITEMS.index("test_mode"), 1), simulation)
        with self.assertRaises(ValueError):
            GameConfig(play_mode="test")

    def test_god_view_stays_closed_when_not_allowed(self):
        game = Game(config=GameConfig(npc_ai_enabled=False, allow_god_view=False))
        term = Mock()
        term.read_keys.side_effect = [[], ["`"], ["q"]]
        term.caps_lock.return_value = True
        with patch("src.terminal.runtime.Game", return_value=game), patch(
                "src.terminal.text.shutil.get_terminal_size", return_value=(130, 42)), patch(
                "src.terminal.runtime.time.monotonic", side_effect=range(100)):
            play_one(term)
        screens = [ANSI_SGR.sub("", call.args[0]) for call in term.draw.call_args_list]
        self.assertTrue(all("PLAYER VIEW" in screen.splitlines()[0] for screen in screens))
        self.assertNotIn("CAPS", screens[-1])
        self.assertNotIn("Caps Lock", ANSI_SGR.sub("", render_help(game)))

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
        self.assertIn("TEST MODE", plain)
        self.assertNotIn("TASKS 0/", plain.splitlines()[1])
        game.set_role(game.player_id, "crew")
        header = ANSI_SGR.sub("", render_game(game, (120, 38))).splitlines()[1]
        self.assertIn(f"TASKS 0/{len(game.assigned_tasks)}", header)
        self.assertIn("swap role", ANSI_SGR.sub("", render_help(game)))


if __name__ == "__main__":
    unittest.main()
