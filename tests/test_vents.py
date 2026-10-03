"""Vent rules, information boundaries, controllers, and terminal controls."""
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from src import Action, Game, GameConfig
from src.core.models import Body
from src.core.world import EMERGENCY_POS, SPAWNS, VENT_LINKS, VENT_POSITIONS, VENTS
from src.terminal.runtime import play_one
from src.terminal.text import ANSI_SGR, display_width
from src.terminal.ui import map_cell, render_game, render_observer


class VentTests(unittest.TestCase):
    def setUp(self):
        self.config = GameConfig(player_role="impostor", npc_ai_enabled=False,
                                 tasks_per_player=0, end_on_player_death=False)
        self.game = Game(config=self.config)
        self.actor = self.game.player
        self.actor.pos = VENT_POSITIONS["medbay"]

    def use(self, target=None):
        self.actor.move_clock = 0
        return self.game.apply_action(self.actor.id, Action("vent", target=target))

    def test_all_vents_are_walkable_and_connections_are_reciprocal(self):
        self.assertEqual(len(VENTS), 13)
        self.assertEqual(set(VENT_LINKS), set(VENT_POSITIONS))
        for source, targets in VENT_LINKS.items():
            self.assertTrue(self.game.is_walkable(VENT_POSITIONS[source]))
            self.assertTrue(targets)
            self.assertNotIn(source, targets)
            for target in targets:
                self.assertIn(source, VENT_LINKS[target])
                self.actor.vent_id = None
                self.actor.pos = VENT_POSITIONS[source]
                self.assertTrue(self.use())
                self.assertTrue(self.use(target))
                self.assertEqual(self.actor.pos, VENT_POSITIONS[target])
                self.assertTrue(self.use())
                self.assertIsNone(self.actor.vent_id)

    def test_only_living_impostors_nearby_can_enter(self):
        self.actor.pos = (29, 20)
        self.actor.role = "crew"
        self.assertFalse(self.use())
        self.actor.role = "impostor"
        self.actor.alive = False
        self.assertFalse(self.use())
        self.actor.alive = True
        self.actor.pos = (30, 20)
        self.assertFalse(self.use())
        self.actor.pos = (29, 20)
        self.assertFalse(self.use("security"))  # Enter before choosing a destination.
        self.assertTrue(self.use())
        self.assertEqual(self.actor.pos, VENT_POSITIONS["medbay"])

    def test_travel_requires_connection_and_respects_movement_cooldown(self):
        self.assertTrue(self.use())
        self.assertFalse(self.game.apply_action(self.actor.id, Action("vent", "security")))
        self.assertFalse(self.use("cafeteria"))
        self.assertFalse(self.use("unknown"))
        self.assertEqual(self.actor.vent_id, "medbay")
        self.assertTrue(self.use("security"))
        self.assertEqual(self.actor.pos, VENT_POSITIONS["security"])
        self.assertTrue(self.use())
        self.assertIsNone(self.actor.vent_id)

    def test_hidden_actor_does_not_block_floor_and_cannot_exit_onto_player(self):
        self.assertTrue(self.use())
        self.assertFalse(self.game.occupied(self.actor.pos))
        other = self.game.npcs[0]
        other.pos = (29, 20)
        self.assertTrue(self.game.move_entity(other.id, -1, 0))
        self.assertFalse(self.use())
        self.assertEqual(self.actor.vent_id, "medbay")
        self.assertTrue(self.game.move_entity(other.id, 1, 0))
        self.assertTrue(self.use())
        self.assertTrue(self.game.occupied(self.actor.pos))

    def test_vent_blocks_surface_actions_and_pauses_kill_cooldown(self):
        other = self.game.npcs[0]
        other.pos = (29, 20)
        self.game.bodies.append(Body("blue", "Blue", other.pos, "MedBay", 0))
        self.actor.kill_clock = 0
        self.assertTrue(self.use())
        for action in ("left", "right", "up", "down", "kill", "report", "interact", "emergency"):
            self.actor.move_clock = 0
            self.assertFalse(self.game.apply_action(self.actor.id, action), action)
        self.assertFalse(self.game.move_player(1, 0))
        self.assertFalse(self.game.kill(self.actor.id))
        self.assertFalse(self.game.report())
        self.assertFalse(self.game.meetings.call(self.game, self.actor.id))
        self.assertTrue(self.game.apply_action(self.actor.id, Action("chat", message="hello")))
        self.actor.kill_clock = 5
        self.game.tick(1)
        self.assertEqual(self.actor.kill_clock, 5)
        self.assertTrue(self.use())
        self.game.tick(1)
        self.assertAlmostEqual(self.actor.kill_clock, 4)

    def test_hidden_players_and_travel_do_not_leak_but_entry_exit_have_witnesses(self):
        witness, distant = self.game.npcs[:2]
        witness.pos = (29, 20)
        self.assertTrue(self.use())
        events = self.game.events.for_player(witness.id)
        self.assertIsNotNone(self.actor.vent_id)
        self.assertTrue(any(event["kind"] == "vent_entered" for event in events))
        self.assertGreater(witness.suspicion[self.actor.id], 0)
        self.assertFalse(any(event["kind"].startswith("vent_")
                             for event in self.game.events.for_player(distant.id)))
        self.assertFalse(self.game.can_see(self.actor.id, witness.pos))
        self.assertEqual(self.game.visible_positions(self.actor.id), set())
        visible = self.game.visible_positions(witness.id)
        self.game.discovered.update(visible)
        self.assertEqual(ANSI_SGR.sub("", map_cell(self.game, self.actor.pos, visible)), "▣")
        self.assertTrue(self.use("security"))
        self.assertFalse(any(event["kind"] == "vent_traveled"
                             for event in self.game.events.for_player(witness.id)))
        witness.pos = (23, 23)
        self.assertTrue(self.use())
        events = self.game.events.for_player(witness.id)
        self.assertIsNone(self.actor.vent_id)
        self.assertTrue(self.game.can_see(witness.id, self.actor.pos))
        self.assertTrue(any(event["kind"] == "vent_exited" for event in events))

    def test_meeting_clears_vents_even_when_impostor_is_ejected(self):
        for eject in (False, True):
            game = Game(config=self.config)
            game.player_pos = VENT_POSITIONS["medbay"]
            self.assertTrue(game.apply_action("cyan", "vent"))
            reporter = game.npcs[0]
            reporter.pos = EMERGENCY_POS
            self.assertTrue(game.call_emergency(reporter.id))
            self.assertIsNone(game.player.vent_id)
            self.assertFalse(game.apply_action("cyan", "vent"))
            game.meetings.resolve(game, {actor.id: "cyan" if eject else None for actor in game.players})
            self.assertTrue(all(actor.vent_id is None for actor in game.players))
            if not eject:
                self.assertEqual(game.player_pos, SPAWNS[0])

    def test_vent_details_are_detached_and_invalid_travel_does_not_move(self):
        self.assertTrue(self.use())
        position = self.actor.pos
        self.assertFalse(self.use('bad-id'))
        self.assertEqual(self.actor.pos, position)
        details = self.game.vents.observe(self.game, self.actor.id)
        self.assertEqual(details['current'], 'medbay')
        details['connections'][0]['position'][0] = -999
        self.assertEqual(VENT_POSITIONS['security'], (24, 23))
        self.assertEqual(self.game.vents.observe(self.game, 'red')['connections'], [])
        self.assertTrue(self.use('security'))
        self.assertEqual(self.game.vents.observe(self.game, self.actor.id)['current'], 'security')


    def test_builtin_impostor_uses_vent_shortcut_and_crew_paths_do_not(self):
        goal = (23, 23)
        plain_path = self.game.world.find_path(self.actor.pos, goal)
        vent_path = self.game.world.find_path(self.actor.pos, goal, use_vents=True)
        self.assertLess(len(vent_path), len(plain_path))
        self.assertTrue(all(self.game.distance(a, b) == 1 for a, b in zip(plain_path, plain_path[1:])))
        with patch.object(self.game.npc_system, "choose_goal", return_value=goal):
            for _ in range(4):
                self.actor.move_clock = 0
                self.game.move_npc(self.actor)
        self.assertEqual(self.actor.pos, goal)
        self.assertIsNone(self.actor.vent_id)
        kinds = [event["kind"] for event in self.game.events.snapshot()]
        self.assertIn("vent_entered", kinds)
        self.assertIn("vent_traveled", kinds)
        self.assertIn("vent_exited", kinds)

    def test_terminal_controls_and_hints_work_with_hidden_panel_and_small_screen(self):
        self.assertTrue(self.use())
        for size in ((60, 20), (133, 49)):
            for panel in (False, True):
                self.game.panel_visible = panel
                for render in (render_game, render_observer):
                    screen = ANSI_SGR.sub("", render(self.game, size))
                    self.assertIn("V exit", screen)
                    self.assertIn("1 Security", screen)
                    self.assertIn("2 Electrical", screen)
                    self.assertTrue(all(display_width(line) < size[0] for line in screen.splitlines()))
        self.actor.vent_id = None
        self.actor.move_clock = 0
        terminal = Mock()
        terminal.read_keys.side_effect = [["v"], ["1"], ["v"], ["q"]]
        with patch("src.terminal.runtime.Game", return_value=self.game), \
                patch("src.terminal.runtime.time.sleep"), \
                patch("src.terminal.runtime.check_terminal_size", return_value=None), \
                patch.object(self.game, "tick", side_effect=lambda dt: setattr(self.actor, "move_clock", 0)):
            play_one(terminal)
        self.assertEqual(self.actor.pos, VENT_POSITIONS["security"])
        self.assertIsNone(self.actor.vent_id)

    def test_simulation_ignores_vent_keys(self):
        game = Game(config=replace(self.config, play_mode="simulation"))
        game.player_pos = VENT_POSITIONS["medbay"]
        terminal = Mock()
        terminal.read_keys.side_effect = [["v", "1", "2"], ["q"]]
        with patch("src.terminal.runtime.Game", return_value=game), \
                patch("src.terminal.runtime.time.sleep"), \
                patch("src.terminal.runtime.check_terminal_size", return_value=None), \
                patch.object(game, "tick"), patch.object(game.vents, "use") as use:
            play_one(terminal)
        use.assert_not_called()


if __name__ == "__main__":
    unittest.main()
