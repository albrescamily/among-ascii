"""Terminal social-deduction game and shared rules."""
from .core.config import GameConfig
from .core.engine import Game
from .core.models import Action, COLORS

__all__ = ["Action", "COLORS", "Game", "GameConfig"]
