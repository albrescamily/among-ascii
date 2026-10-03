#!/usr/bin/env python3
"""Launch Among Ascii. Game rules and configuration live in the src package."""
if __package__:
    from .src import Action, Game, GameConfig
    from .src.cli import main
else:
    from src import Action, Game, GameConfig
    from src.cli import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nGame closed.")
        raise SystemExit(130)
