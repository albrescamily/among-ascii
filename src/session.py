"""Composition root: builds a match and attaches the controllers its mode needs."""
from __future__ import annotations
from typing import Optional
from .controllers.npcs import NPCController
from .core.config import GameConfig
from .core.engine import Game


def new_game(seed: Optional[int] = None, config: Optional[GameConfig] = None) -> Game:
    """A match ready to play: Game mode gets the built-in NPCs; Test and Simulation modes get none."""
    game = Game(seed=seed, config=config)
    if game.config.play_mode == "game" and not game.test_mode:
        game.controllers.append(NPCController())
    return game


def npc_controller(game: Game) -> Optional[NPCController]:
    return next((controller for controller in game.controllers if isinstance(controller, NPCController)), None)
