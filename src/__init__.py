"""Terminal social-deduction game and shared rules."""
from .core.config import GameConfig
from .core.engine import Game
from .core.models import Action, COLORS
from .session import new_game

__all__ = ["Action", "COLORS", "Game", "GameConfig", "new_game"]
