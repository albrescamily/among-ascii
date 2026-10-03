"""Shared configuration, events, rules and interactive launcher contracts."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src import COLORS, Game, GameConfig
from src.core.events import EventLog

PROJECT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def test_twelve_distinct_colors_and_safe_spawns(self):
        game = Game()
        self.assertEqual(len(game.players), 12)
        self.assertEqual({actor.name for actor in game.players}, {color.name for color in COLORS})
        for attribute in ("id", "symbol", "color", "pos"):
            self.assertEqual(len({getattr(actor, attribute) for actor in game.players}), 12)
        for actor in game.players:
            self.assertEqual(actor.id, actor.name.lower())
            self.assertTrue(game.is_walkable(actor.pos))
        self.assertEqual(game.player.name, "Cyan")

    def test_role_count_color_and_configuration_roundtrip(self):
        game = Game(config=GameConfig(player_count=8, player_color="Purple",
                                      player_role="impostor", impostor_count=3, tasks_per_player=2))
        self.assertEqual(len(game.players), 8)
        self.assertEqual(game.player.name, "Purple")
        self.assertEqual(game.player.role, "impostor")
        self.assertEqual(len(game.impostor_ids), 3)
        for actor in game.players:
            self.assertEqual(len(game.tasks.states[actor.id].assigned), 2 if actor.role == "crew" else 0)
        self.assertEqual(GameConfig.from_dict(game.config.to_dict()), game.config)

    def test_invalid_configuration_is_rejected(self):
        cases = [{"player_count": 13}, {"impostor_count": 6}, {"player_color": "Azul"},
                 {"tasks_per_player": 25}, {"task_seconds": 0}, {"vision_radius": -1},
                 {"kill_cooldown": float("nan")}, {"seed": True}, {"npc_ai_enabled": "false"},
                 {"missing_option": 1}, {"player_role": "impostor", "impostor_count": 0}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                GameConfig.from_dict(value)


class EventAndLauncherTests(unittest.TestCase):
    def test_event_history_is_bounded_filtered_and_detached(self):
        log = EventLog(2)
        log.emit(0, "expired", "old")
        payload = {"position": [1, 2]}
        log.emit(1, "private", "secret", data=payload, visible_to=["red"])
        log.emit(2, "public", "hello")
        payload["position"][0] = 99
        self.assertEqual([event["kind"] for event in log.for_player("blue")], ["public"])
        red_events = log.for_player("red")
        self.assertEqual(red_events[0]["data"]["position"], [1, 2])
        red_events[0]["data"]["position"][0] = 77
        self.assertEqual(log.snapshot()[0]["data"]["position"], [1, 2])
        self.assertEqual(len(log.snapshot(after=2)), 1)

    def test_launcher_help_has_no_agent_experiment_commands(self):
        process = subprocess.run([sys.executable, '-X', 'utf8', 'game.py', '--help'],
                                 cwd=PROJECT, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('--mode', process.stdout)
        for removed in ('--agent-server', '--simulate', '--episodes', '--log'):
            self.assertNotIn(removed, process.stdout)

    def test_launcher_exports_validated_settings(self):
        with tempfile.NamedTemporaryFile(dir=PROJECT, prefix='.test-config-', suffix='.json', delete=False) as handle:
            path = Path(handle.name)
        try:
            process = subprocess.run(
                [sys.executable, '-X', 'utf8', 'game.py', '--mode', 'simulation', '--players', '8',
                 '--dump-config', str(path)],
                cwd=PROJECT, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            config = GameConfig.load(path)
            self.assertEqual(config.play_mode, 'simulation')
            self.assertEqual(config.player_count, 8)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
