"""Shared sabotage rules and Game/Simulation terminal controls."""
import unittest
from unittest.mock import Mock, patch

from src import Action, Game, GameConfig
from src.core.models import Body
from src.core.sabotage import CRITICAL, PANELS, SABOTAGE_KEYS
from src.core.world import EMERGENCY_POS, ROOM_LABELS
from src.terminal.palette import RED, YELLOW
from src.terminal.runtime import play_one, trigger_sabotage
from src.terminal.text import ANSI_SGR, display_width
from src.terminal.ui import map_cell, observer_map_cell, render_game, render_observer


def make_game(mode="game", **overrides):
    options = dict(play_mode=mode, npc_ai_enabled=False, kills_enabled=False,
                   task_win_mode="disabled", initial_sabotage_cooldown=0)
    options.update(overrides)
    return Game(config=GameConfig(**options))


def start(game, kind):
    return game.apply_action(game.impostor_id, Action("sabotage", kind))


class SabotageTests(unittest.TestCase):
    def test_default_countdown_and_timeout_in_both_modes(self):
        for mode in ("game", "simulation"):
            for kind in CRITICAL:
                with self.subTest(mode=mode, kind=kind):
                    game = make_game(mode)
                    self.assertTrue(start(game, kind))
                    self.assertEqual(game.sabotage.remaining, 40)
                    game.tick(39)
                    self.assertIsNone(game.outcome)
                    game.tick(1)
                    self.assertEqual(game.winner, "impostor")
                    self.assertAlmostEqual(game.elapsed, 40)

    def test_twenty_second_cooldown_after_repair(self):
        for mode in ("game", "simulation"):
            game = make_game(mode)
            start(game, "admin")
            game.player_pos = PANELS["admin"][0].pos
            self.assertTrue(game.apply_action(game.player_id, "interact"))
            game.tick(3)
            self.assertIsNone(game.sabotage.kind)
            self.assertAlmostEqual(game.sabotage.cooldown, 20)
            game.tick(19)
            self.assertFalse(start(game, "o2"))
            game.tick(1)
            self.assertTrue(start(game, "o2"))

    def test_reactor_needs_two_simultaneous_workers(self):
        for mode in ("game", "simulation"):
            game = make_game(mode)
            crew = [a for a in game.players if a.role == "crew"]
            start(game, "reactor")
            crew[0].pos = PANELS["reactor"][0].pos
            game.interact_entity(crew[0].id)
            game.tick(4)
            self.assertEqual(game.sabotage.progress[0], 0)
            crew[1].pos = PANELS["reactor"][1].pos
            game.interact_entity(crew[1].id)
            game.tick(1)
            self.assertGreater(game.sabotage.progress[0], 0)
            self.assertTrue(game.move_entity(crew[1].id, 1, 0))
            game.tick(1)
            self.assertFalse(any(game.sabotage.progress.values()))
            game.interact_entity(crew[1].id)
            game.tick(3)
            self.assertIsNone(game.sabotage.kind)

    def test_o2_panels_can_be_repaired_sequentially(self):
        for mode in ("game", "simulation"):
            game = make_game(mode)
            start(game, "o2")
            game.player_pos = PANELS["o2"][0].pos
            game.interact()
            game.tick(3)
            self.assertEqual(game.sabotage.completed, {0})
            self.assertEqual(game.sabotage.kind, "o2")
            game.player_pos = PANELS["o2"][1].pos
            game.interact()
            game.tick(3)
            self.assertIsNone(game.sabotage.kind)

    def test_role_cooldown_enabled_and_overlap_rules(self):
        for mode in ("game", "simulation"):
            game = make_game(mode, initial_sabotage_cooldown=2)
            self.assertFalse(start(game, "reactor"))
            game.tick(2)
            self.assertFalse(game.apply_action(game.player_id, Action("sabotage", "reactor")))
            game.entity(game.impostor_id).alive = False
            self.assertFalse(start(game, "reactor"))
            game.entity(game.impostor_id).alive = True
            self.assertTrue(start(game, "reactor"))
            self.assertFalse(start(game, "o2"))
            disabled = make_game(mode, sabotage_enabled=False)
            self.assertFalse(start(disabled, "admin"))

    def test_repairs_cancel_on_death_or_vent(self):
        for state in ("dead", "vented"):
            game = make_game("simulation")
            start(game, "admin")
            worker = game.entity(game.impostor_id)
            worker.pos = PANELS["admin"][0].pos
            game.interact_entity(worker.id)
            game.tick(1)
            if state == "dead":
                worker.alive = False
            else:
                worker.vent_id = "admin"
            game.sabotage.tick(game, 3)
            self.assertEqual(game.sabotage.kind, "admin")
            self.assertFalse(game.sabotage.workers)
            self.assertEqual(game.sabotage.progress[0], 0)

    def test_meetings_and_reports(self):
        for mode in ("game", "simulation"):
            game = make_game(mode)
            start(game, "reactor")
            game.player_pos = EMERGENCY_POS
            self.assertFalse(game.call_emergency(game.player_id))
            victim = next(a for a in game.players[1:] if a.role == "crew")
            victim.alive = False
            game.bodies.append(Body(victim.id, victim.name, game.player_pos, "Cafeteria", 0, []))
            self.assertTrue(game.report())
            self.assertIsNone(game.sabotage.kind)
            self.assertFalse(start(game, "o2"))
            self.assertEqual(game.sabotage.cooldown, 20)

    def test_simulation_does_not_add_autonomous_behavior(self):
        game = make_game("simulation", initial_sabotage_cooldown=1)
        positions = [a.pos for a in game.players]
        game.tick(30)
        self.assertIsNone(game.sabotage.kind)
        self.assertIsNone(game.npc_system)
        self.assertTrue(trigger_sabotage(game, "f"))
        game.tick(3)
        self.assertEqual(positions, [a.pos for a in game.players])
        self.assertFalse(game.sabotage.workers)
        self.assertEqual(game.npcs, [])

    def test_npcs_still_repair_all_emergencies_in_game(self):
        for kind in PANELS:
            game = make_game(npc_ai_enabled=True, player_role="impostor")
            start(game, kind)
            for _ in range(400):
                game.tick(.1)
                if game.outcome or not game.sabotage.kind:
                    break
            self.assertIsNone(game.outcome, kind)
            self.assertIsNone(game.sabotage.kind, kind)

    def test_panel_geometry_and_validation(self):
        game = make_game()
        for panels in PANELS.values():
            for panel in panels:
                x, y = panel.pos
                self.assertEqual(game.grid[y][x], ".")
                self.assertNotIn(panel.pos, ROOM_LABELS)
                self.assertTrue(game.find_path(game.player_pos, panel.pos))
        for target in (None, 3, "comms"):
            with self.assertRaises(ValueError):
                Action.parse(Action("sabotage", target))
        for options in ({"sabotage_seconds": 0}, {"sabotage_cooldown": -1},
                        {"sabotage_repair_seconds": 0}, {"sabotage_enabled": "yes"}):
            with self.assertRaises(ValueError):
                GameConfig(**options)


class LightsSabotageTests(unittest.TestCase):
    def test_dims_only_crew_vision_until_repaired(self):
        for mode in ("game", "simulation"):
            game = make_game(mode)
            crew = next(a for a in game.players if a.role == "crew")
            impostor = game.entity(game.impostor_id)
            full = game.config.vision_radius
            self.assertEqual(game.vision_radius(crew.id), full)
            self.assertTrue(start(game, "lights"))
            self.assertEqual(game.vision_radius(crew.id), game.config.lights_vision_radius)
            self.assertEqual(game.vision_radius(impostor.id), full)
            self.assertTrue(all(abs(x - crew.pos[0]) <= 2 and abs(y - crew.pos[1]) <= 2
                                for x, y in game.visible_positions(crew.id)))
            game.tick(60)
            self.assertIsNone(game.outcome)
            self.assertEqual(game.sabotage.kind, "lights")
            crew.pos = PANELS["lights"][0].pos
            self.assertTrue(game.interact_entity(crew.id))
            game.tick(3)
            self.assertIsNone(game.sabotage.kind)
            self.assertEqual(game.vision_radius(crew.id), full)

    def test_tasks_and_emergency_button_stay_available(self):
        game = make_game(player_role="crew")
        start(game, "lights")
        self.assertTrue(game.emergency_available)
        game.player_pos = EMERGENCY_POS
        self.assertTrue(game.call_emergency(game.player_id))
        self.assertIsNone(game.sabotage.kind)

    def test_map_turns_red_without_countdown(self):
        game = make_game()
        wall = (38, 1)
        start(game, "lights")
        self.assertIn(RED, observer_map_cell(game, wall))
        self.assertIn(RED, map_cell(game, wall, set()))
        plain = ANSI_SGR.sub("", render_game(game, (120, 38)))
        self.assertIn("LIGHTS OUT | crew vision reduced", plain)


class SabotageTerminalTests(unittest.TestCase):
    def test_hotkeys_work_in_game_and_simulation_with_a_crew_observer(self):
        for mode in ("game", "simulation"):
            for key, kind in SABOTAGE_KEYS.items():
                game = make_game(mode, player_role="impostor" if mode == "game" else "crew")
                term = Mock()
                term.read_keys.side_effect = [[key], ["q"]]
                with patch("src.terminal.runtime.Game", return_value=game), patch(
                        "src.terminal.text.shutil.get_terminal_size", return_value=(120, 38)):
                    play_one(term)
                self.assertEqual(game.sabotage.kind, kind)

    def test_spectator_has_no_bypass_for_missing_impostors_or_cooldown(self):
        game = make_game("simulation", impostor_count=0)
        self.assertFalse(trigger_sabotage(game, "f"))
        game = make_game("simulation", initial_sabotage_cooldown=25)
        self.assertFalse(trigger_sabotage(game, "f"))
        game.tick(25)
        self.assertTrue(trigger_sabotage(game, "f"))
        self.assertFalse(trigger_sabotage(game, "g"))
        self.assertFalse(trigger_sabotage(make_game(), "f"))

    def test_red_alert_and_countdown_fit_and_restore(self):
        for mode in ("game", "simulation"):
            game = make_game(mode)
            wall = (38, 1)
            self.assertIn(YELLOW, observer_map_cell(game, wall))
            start(game, "reactor")
            game.tick(1)
            self.assertIn(RED, observer_map_cell(game, wall))
            for size in ((60, 20), (120, 38), (230, 49)):
                for panel in (False, True):
                    game.panel_visible = panel
                    for renderer in (render_game, render_observer):
                        screen = renderer(game, size)
                        plain = ANSI_SGR.sub("", screen)
                        self.assertIn("REACTOR MELTDOWN | 39s", plain)
                        self.assertLessEqual(len(plain.splitlines()), size[1] - 1)
                        self.assertTrue(all(display_width(line) <= size[0] - 1 for line in screen.splitlines()))
                        self.assertNotIn("\x1b", plain)
            game.sabotage.clear(game)
            self.assertIn(YELLOW, observer_map_cell(game, wall))

    def test_markers_do_not_reveal_hidden_players(self):
        game = make_game()
        start(game, "o2")
        pos = PANELS["o2"][0].pos
        actor = game.players[1]
        actor.pos = pos
        self.assertEqual(ANSI_SGR.sub("", map_cell(game, pos, set())), "!")
        self.assertIn(actor.symbol, observer_map_cell(game, pos))
