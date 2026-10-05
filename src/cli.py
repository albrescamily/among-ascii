"""Interactive game launcher and JSON configuration."""
from __future__ import annotations
import argparse
import json
from dataclasses import replace
from pathlib import Path
from .core.config import GameConfig
from .core.models import COLORS
from .terminal.runtime import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Among Ascii — terminal social-deduction game")
    parser.add_argument("--config", help="JSON settings file")
    parser.add_argument("--seed", type=int, help="deterministic random seed")
    parser.add_argument("--mode", choices=["game", "simulation"], help="game with NPCs or inactive simulation scaffold")
    parser.add_argument("--test-mode", action=argparse.BooleanOptionalAction, default=None,
                        help="game-mode sandbox: idle NPCs, X swaps role, N resets")
    parser.add_argument("--allow-god-view", action=argparse.BooleanOptionalAction, default=None,
                        help="let Caps Lock open the God view in game mode")
    parser.add_argument("--players", type=int, help="total players, including the local player (1-12)")
    parser.add_argument("--impostors", type=int, help="number of impostors")
    parser.add_argument("--player-color", choices=[color.name for color in COLORS])
    parser.add_argument("--player-role", choices=["crew", "impostor", "random"])
    parser.add_argument("--tasks", type=int, help="tasks assigned to each crewmate (0-24)")
    parser.add_argument("--dump-config", metavar="PATH", help="write validated settings to JSON and exit")
    args = parser.parse_args(argv)
    try:
        config = GameConfig.load(args.config) if args.config else GameConfig()
        overrides = {name: value for name, value in {
            "seed": args.seed, "play_mode": args.mode, "player_count": args.players, "impostor_count": args.impostors,
            "player_color": args.player_color, "player_role": args.player_role,
            "tasks_per_player": args.tasks, "test_mode": args.test_mode,
            "allow_god_view": args.allow_god_view}.items() if value is not None}
        config = replace(config, **overrides)
        if args.dump_config:
            Path(args.dump_config).write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
            return 0
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return run(config=config)
