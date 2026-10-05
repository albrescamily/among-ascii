"""Startup settings and inactive simulation scaffold."""
import unittest
from unittest.mock import Mock, patch

from src import Game, GameConfig
from src.core.world import EMERGENCY_POS
from src.terminal.menu import MENU_ITEMS, adjust_setting, configure_game, normalize_settings, render_setup
from src.terminal.runtime import conduct_meeting, play_one, run
from src.terminal.text import ANSI_SGR, display_width


class StartupTests(unittest.TestCase):
    def test_setup_edits_mode_players_cooldown_tasks_and_voting_before_play(self):
        terminal = Mock()
        terminal.read_keys.side_effect = [
            ["right"], ["down"], ["left"], ["down"], ["right", "right"],
            ["down"], ["left"], ["down"], ["down"], ["right"], ["down"], ["down"], ["down"], ["\r"],
        ]
        with patch("src.terminal.menu.time.sleep"):
            settings = configure_game(terminal, GameConfig())
        self.assertEqual(settings.play_mode, "simulation")
        self.assertEqual(settings.player_count, 11)
        self.assertEqual(settings.tasks_per_player, 4)
        self.assertEqual(settings.kill_cooldown, 12)
        self.assertEqual(settings.initial_kill_cooldown, 12)
        self.assertEqual(settings.post_meeting_kill_cooldown, 12)
        self.assertEqual(settings.voting_seconds, 35)
        game = Game(config=settings)
        self.assertTrue(all(actor.kill_clock == 12 for actor in game.players))
        self.assertEqual(len(game.players), 11)
        self.assertEqual(sum(len(state.assigned) for state in game.tasks.states.values()), 40)

    def test_setting_bounds_and_cancel_without_starting(self):
        config = GameConfig(kill_cooldown=0, tasks_per_player=0)
        self.assertEqual(adjust_setting(config, 2, -1).kill_cooldown, 0)
        self.assertEqual(adjust_setting(GameConfig(kill_cooldown=60), 2, 1).kill_cooldown, 60)
        self.assertEqual(adjust_setting(config, 3, -1).tasks_per_player, 0)
        self.assertEqual(adjust_setting(GameConfig(tasks_per_player=24), 3, 1).tasks_per_player, 24)
        self.assertEqual(adjust_setting(GameConfig(player_count=4), 1, -1).player_count, 4)
        self.assertEqual(adjust_setting(GameConfig(player_count=12), 1, 1).player_count, 12)
        self.assertEqual(adjust_setting(GameConfig(voting_seconds=5), 4, -1).voting_seconds, 5)
        self.assertEqual(adjust_setting(GameConfig(voting_seconds=120), 4, 1).voting_seconds, 120)
        terminal = Mock()
        terminal.read_keys.return_value = ["escape"]
        self.assertIsNone(configure_game(terminal, config))
        with self.assertRaises(ValueError):
            GameConfig(play_mode="unknown")

    def test_reducing_players_keeps_impostor_count_and_game_valid(self):
        config = GameConfig(player_count=12, impostor_count=5, player_role="impostor")
        for _ in range(10):
            config = adjust_setting(config, 1, -1)
            self.assertGreaterEqual(config.player_count, 4)
            self.assertLess(config.impostor_count * 2, config.player_count)
        self.assertEqual((config.player_count, config.impostor_count), (4, 1))
        game = Game(config=config)
        self.assertEqual(len(game.players), 4)
        self.assertEqual(game.player.role, "impostor")

    def test_imported_settings_are_bounded_before_play(self):
        original = GameConfig(player_count=1, impostor_count=0, kill_cooldown=120, voting_seconds=180)
        terminal = Mock()
        terminal.read_keys.return_value = ["down"] * MENU_ITEMS.index("play") + ["\r"]
        selected = configure_game(terminal, original)
        self.assertEqual(selected, normalize_settings(original))
        self.assertEqual(selected.player_count, 4)
        self.assertEqual(selected.kill_cooldown, 60)
        self.assertEqual(selected.initial_kill_cooldown, 60)
        self.assertEqual(selected.post_meeting_kill_cooldown, 60)
        self.assertEqual(selected.voting_seconds, 120)
        self.assertEqual(original.player_count, 1)
        self.assertEqual(original.kill_cooldown, 120)

    def test_setup_screen_fits_and_centers_at_supported_sizes(self):
        for mode in ("game", "simulation"):
            for size in ((60, 20), (80, 24), (120, 38), (230, 48)):
                with self.subTest(mode=mode, size=size):
                    screen = ANSI_SGR.sub("", render_setup(GameConfig(play_mode=mode), MENU_ITEMS.index("play"), size))
                    lines = screen.splitlines()
                    self.assertLessEqual(len(lines), size[1] - 1)
                    self.assertTrue(all(display_width(line) <= size[0] - 1 for line in lines))
                    top = next(index for index, line in enumerate(lines) if "╔" in line)
                    bottom = next(index for index, line in enumerate(lines) if "╚" in line)
                    left = lines[top].index("╔")
                    self.assertLessEqual(abs(left - (size[0] - 1 - display_width(lines[top]))), 1)
                    self.assertLessEqual(abs(top - (size[1] - 2 - bottom)), 1)
                    self.assertIn("> [ PLAY ]", screen)
                    self.assertIn("55 tasks total", screen)
                    self.assertIn("Total players", screen)
                    self.assertNotIn("min", screen.lower())
                    self.assertNotIn("max", screen.lower())
                    self.assertIn("Voting time", screen)
        screen = render_setup(GameConfig(), size=(30, 10))
        self.assertIn("Terminal too small", screen)

    def test_run_uses_setup_and_preserves_settings_when_restarting(self):
        settings = GameConfig(kill_cooldown=17, tasks_per_player=2)
        terminal = Mock()
        terminal.read_keys.return_value = ["r"]
        with patch("src.terminal.runtime.sys.stdin.isatty", return_value=True), \
                patch("src.terminal.runtime.sys.stdout.isatty", return_value=True), \
                patch("src.terminal.runtime.Terminal") as driver, \
                patch("src.terminal.runtime.configure_game", side_effect=[settings, None]) as setup, \
                patch("src.terminal.runtime.play_one", return_value=Game(config=settings)) as play:
            driver.return_value.__enter__.return_value = terminal
            self.assertEqual(run(), 0)
        play.assert_called_once_with(terminal, seed=None, config=settings)
        self.assertEqual(setup.call_args_list[-1].args, (terminal, settings))


class SimulationModeTests(unittest.TestCase):
    def test_game_npcs_keep_moving_and_completing_tasks(self):
        game = Game(config=GameConfig(impostor_count=0, task_seconds=0.1))
        self.assertEqual(game.npcs, game.players[1:])
        self.assertIsNotNone(game.npc_system)
        local_position = game.player_pos
        positions = [actor.pos for actor in game.npcs]
        worker = game.npcs[0]
        task = game.tasks.states[worker.id].assigned[0]
        worker.pos = task
        for _ in range(10):
            game.tick(0.1)
            self.assertEqual(len({actor.pos for actor in game.players}), len(game.players))
        self.assertIn(task, game.tasks.states[worker.id].completed)
        self.assertNotEqual(positions[1:], [actor.pos for actor in game.npcs[1:]])
        self.assertEqual(game.player_pos, local_position)

    def test_simulation_renders_every_inactive_player(self):
        from src.terminal.ui import observer_map_cell, render_observer

        game = Game(config=GameConfig(play_mode="simulation"))
        screen = ANSI_SGR.sub("", render_observer(game, (133, 49)))
        self.assertIn("NO CONTROLLER", screen)
        self.assertNotIn("BUILT-IN NPC", screen)
        self.assertNotIn("HUNTING", screen)
        for actor in game.players:
            self.assertIn(actor.name, screen)
            self.assertEqual(ANSI_SGR.sub("", observer_map_cell(game, actor.pos)), actor.symbol)

    def test_simulation_has_no_controllers_or_automatic_actions(self):
        game = Game(config=GameConfig(play_mode='simulation', initial_kill_cooldown=0))
        self.assertEqual(game.npcs, [])
        self.assertIsNone(game.npc_system)
        self.assertFalse(game.config.npc_ai_enabled)
        self.assertFalse(game.config.end_on_player_death)
        positions = [actor.pos for actor in game.players]
        for _ in range(30):
            game.tick(1)
        self.assertEqual([actor.pos for actor in game.players], positions)
        self.assertTrue(all(actor.alive and actor.goal is None and not actor.path for actor in game.players))
        self.assertTrue(all(state.active is None and not state.completed for state in game.tasks.states.values()))
        self.assertEqual(game.chat.snapshot(), [])
        self.assertEqual(game.bodies, [])
        self.assertIsNone(game.pending_meeting)
        self.assertIsNone(game.outcome)
        self.assertEqual([event['kind'] for event in game.events.snapshot()], ['game_started'])

    def test_simulation_ignores_gameplay_keys_and_keeps_display_controls(self):
        game = Game(config=GameConfig(play_mode="simulation"))
        terminal = Mock()
        terminal.read_keys.side_effect = [["w", "e", "r", "k", "\t", "z", "p"], ["q"]]
        with patch("src.terminal.runtime.Game", return_value=game), \
                patch.object(game, "tick"), patch.object(game, "move_player") as move, \
                patch.object(game, "interact") as interact, patch.object(game, "report") as report, \
                patch.object(game, "kill") as kill, patch("src.terminal.runtime.time.sleep"):
            self.assertIs(play_one(terminal), game)
        for action in (move, interact, report, kill):
            action.assert_not_called()
        self.assertFalse(game.panel_visible)
        self.assertIn("SIMULATION", terminal.draw.call_args_list[0].args[0])
        self.assertIsNone(game.winner)
        self.assertEqual(game.outcome_reason, "Simulation stopped.")

    def test_simulation_meeting_deadline_skips_without_npc_votes(self):
        game = Game(config=GameConfig(play_mode="simulation", vote_result_seconds=0.8, discussion_seconds=0))
        game.player_pos = EMERGENCY_POS
        self.assertTrue(game.call_emergency(game.player_id))
        terminal = Mock()
        terminal.read_keys.return_value = []
        with patch.object(game.meetings, "bot_vote", return_value=None) as vote, \
                patch("src.terminal.runtime.time.monotonic", side_effect=[0, 0.1, 30, 30, 30.1, 31]), \
                patch("src.terminal.runtime.time.sleep"):
            conduct_meeting(terminal, game)
        vote.assert_not_called()
        self.assertEqual(game.meetings.last_result, (None, {None: 12}))
        self.assertEqual(game.chat.snapshot(), [])
        self.assertIsNone(game.pending_meeting)
        self.assertEqual(game.meeting_number, 1)
        self.assertEqual(game.elapsed, 0)
        self.assertIn("Simulation resumes automatically", terminal.draw.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
