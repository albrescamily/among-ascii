"""Controller layer: the core only knows the protocol; NPCs, the player and agents plug into it."""
import ast
import unittest
from pathlib import Path

from src import Game, GameConfig, new_game
from src.controllers.player import PlayerController
from src.controllers.npcs import NPCController
from src.core.engine import TICK_STEP
from src.core.world import EMERGENCY_POS
from src.session import npc_controller
from src.terminal.runtime import advance

SOURCE = Path(__file__).resolve().parents[1] / "src"


class RecordingController:
    """The smallest controller an agent could start from: it only records the engine hooks."""

    def __init__(self):
        self.calls = []

    def tick(self, game):
        self.calls.append("tick")

    def meeting_started(self, game):
        self.calls.append("meeting_started")

    def meeting_tick(self, game):
        self.calls.append("meeting_tick")


def imported_modules(path: Path) -> list[str]:
    """Absolute names of everything a source file imports, resolving relative imports."""
    package = ["src", *path.relative_to(SOURCE).parent.parts]
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[:len(package) - node.level + 1] if node.level else []
            names.append(".".join(base + (node.module.split(".") if node.module else [])))
    return names


class ControllerLayerTests(unittest.TestCase):
    def test_core_imports_nothing_outside_core(self):
        for path in (SOURCE / "core").rglob("*.py"):
            for name in imported_modules(path):
                with self.subTest(file=path.name, module=name):
                    self.assertFalse(name.startswith("src") and not name.startswith("src.core"))

    def test_game_mode_gets_npcs_and_other_modes_run_without_controllers(self):
        self.assertIsInstance(npc_controller(new_game()), NPCController)
        self.assertEqual(new_game(config=GameConfig(test_mode=True)).controllers, [])
        self.assertEqual(new_game(config=GameConfig(play_mode="simulation")).controllers, [])

    def test_any_controller_receives_the_engine_hooks(self):
        recorder = RecordingController()
        game = Game(config=GameConfig(play_mode="simulation"), controllers=[recorder])
        game.tick(3 * TICK_STEP)
        game.player_pos = EMERGENCY_POS
        self.assertTrue(game.call_emergency(game.player_id))
        game.tick(1.0)
        self.assertEqual(recorder.calls, ["tick"] * 3 + ["meeting_started", "meeting_tick"])

    def test_player_moves_obey_the_cooldown_and_keep_an_early_key(self):
        game = new_game(config=GameConfig(npc_ai_enabled=False))
        player = PlayerController()
        game.player_pos = (44, 6)  # Open Cafeteria floor.
        self.assertTrue(player.act(game, "left"))
        self.assertFalse(player.act(game, "left"))  # Cooling down: buffered, not lost.
        self.assertEqual(game.player_pos, (43, 6))
        game.tick(TICK_STEP)
        player.update(game)
        self.assertEqual(game.player_pos, (43, 6))  # 0.05 s is shorter than player_move_seconds.
        game.tick(2 * TICK_STEP)
        player.update(game)
        self.assertEqual(game.player_pos, (42, 6))
        self.assertFalse(player.act(game, "left"))
        game.tick(1.0)
        player.update(game)
        self.assertEqual(game.player_pos, (42, 6))  # A stale key expires instead of moving later.

    def test_fixed_steps_replay_the_same_match_at_any_frame_rate(self):
        # Passing raw frame times to Game.tick diverges at 60 fps; whole steps do not.
        def play(frame):
            game = new_game(config=GameConfig(seed=7, meetings_enabled=False))
            pending = 0.0
            while game.elapsed < 20.0 - 1e-6 and not game.outcome:
                pending = advance(game, pending, frame)
            return [(actor.pos, actor.alive) for actor in game.players]

        self.assertEqual(play(1 / 60), play(TICK_STEP))


if __name__ == "__main__":
    unittest.main()
