from __future__ import annotations
import sys
import time
from typing import Optional
from ..core.config import GameConfig
from ..core.engine import Game
from ..core.world import MAP_H, MAP_W
from ..core.sabotage import SABOTAGE_KEYS
from .driver import Terminal
from .menu import configure_game
from .chat import ChatView, render_chat
from .text import MIN_COLUMNS, MIN_ROWS, terminal_size, fit_screen
from .palette import RED, RESET
from .ui import (render_game, render_observer, render_minimap, render_help, render_end, render_meeting,
                 render_vote_result, screen_layout, camera_origin, VOTE_KEYS, sabotage_alert)

MOVE_KEYS = {
    "w": (0, -1), "up": (0, -1),
    "s": (0, 1), "down": (0, 1),
    "a": (-1, 0), "left": (-1, 0),
    "d": (1, 0), "right": (1, 0),
}


def trigger_sabotage(game: Game, key: str) -> bool:
    """The simulation spectator acts on behalf of a living impostor."""
    actor = (next((a for a in game.players if a.alive and a.role == "impostor"), None)
             if game.config.play_mode == "simulation" else game.player)
    if actor is None:
        game.message("Sabotage unavailable: no living impostor.")
        return False
    if game.apply_action(actor.id, {"kind": "sabotage", "target": SABOTAGE_KEYS[key]}):
        return True
    game.message("Sabotage requires a living impostor, no active emergency and a ready cooldown.")
    return False


def pan_camera(game: Game, dx: int, dy: int) -> None:
    layout = screen_layout(game, terminal_size(), observer=True)
    x0, y0 = camera_origin(game, layout)
    x0 = max(0, min(x0 + dx, MAP_W - layout.map_columns))
    y0 = max(0, min(y0 + dy, MAP_H - layout.map_rows))
    game.camera_pos = (x0 + layout.map_columns // 2, y0 + layout.map_rows // 2)


def stop_game(game: Game) -> None:
    if game.config.play_mode == "simulation":
        game.outcome, game.outcome_reason = "stopped", "Simulation stopped."
    elif game.test_mode:
        game.outcome, game.outcome_reason = "stopped", "Test mode closed."
    else:
        game.lose("You left the mission.")


def conduct_meeting(term: Terminal, game: Game) -> None:
    if not game.pending_meeting:
        return
    reporter_id, body = game.pending_meeting
    automatic = game.config.play_mode == "simulation" or not game.player_alive
    prompt = ""
    meeting_chat = ChatView()
    chat_view = None
    last = time.monotonic()
    while game.pending_meeting and not game.outcome:
        now = time.monotonic()
        game.tick(min(60.0, max(0.0, now - last)))
        last = now
        if not game.pending_meeting:
            break
        term.draw(render_chat(game, chat_view) if chat_view is not None
                  else render_meeting(game, reporter_id, body, prompt))
        for key in term.read_keys():
            if chat_view is not None:
                if chat_view.handle_key(game, key):
                    chat_view = None
                continue
            key = key.lower()
            if key in ("q", "escape"):
                stop_game(game)
                game.pending_meeting = None
                return
            if key == "t":
                chat_view = meeting_chat
            elif not automatic and (key == "0" or key in VOTE_KEYS):
                if game.player_id in game.meetings.votes:
                    prompt = "Vote recorded | waiting for other votes"
                    continue
                if key == "0":
                    game.apply_action(game.player_id, "vote")
                    prompt = "Skip recorded | waiting for other votes"
                    continue
                index = VOTE_KEYS.index(key)
                ids = game.alive_ids()
                if index < len(ids):
                    game.apply_action(game.player_id, {"kind": "vote", "target": ids[index]})
                    prompt = "Vote recorded | waiting for other votes"
                else:
                    prompt = RED + "Choose one of the displayed keys." + RESET
        time.sleep(0.03)
    if game.meetings.last_result is None:
        return
    ejected, counts = game.meetings.last_result
    deadline = time.monotonic() + (0.8 if automatic else 3.0)
    while time.monotonic() < deadline:
        term.draw(render_vote_result(game, ejected, counts))
        if term.read_keys():
            break
        time.sleep(0.03)


def check_terminal_size() -> Optional[str]:
    columns, rows = terminal_size()
    if columns < MIN_COLUMNS or rows < MIN_ROWS:
        return f"Terminal too small ({columns}x{rows}). Minimum: {MIN_COLUMNS}x{MIN_ROWS}."
    return None


def play_one(term: Terminal, seed: Optional[int] = None, config: Optional[GameConfig] = None) -> Game:
    game = Game(seed=seed, config=config)
    help_open = False
    simulation = game.config.play_mode == "simulation"
    god_open = False      # Caps Lock on Windows; the ` key elsewhere.
    minimap_open = False  # Tab
    chat_view = None
    last = time.monotonic()
    next_frame = last
    size_warning = check_terminal_size()
    if size_warning:
        game.message(size_warning)

    while not game.outcome:
        now = time.monotonic()
        dt = now - last
        last = now

        if check_terminal_size():
            term.draw(fit_screen([]))
            if any(key.lower() in ("q", "escape") for key in term.read_keys()):
                stop_game(game)
            time.sleep(0.03)
            continue

        caps = term.caps_lock() if hasattr(term, "caps_lock") else None
        if isinstance(caps, bool):
            god_open = caps and game.god_view_allowed
        for key in term.read_keys():
            if chat_view is not None:
                if chat_view.handle_key(game, key):
                    chat_view = None
                continue
            key = key.lower()
            if simulation and key in ("e", "r", "k", "v", "1", "2", "\t"):
                continue
            if key in MOVE_KEYS:
                if simulation:
                    pan_camera(game, *MOVE_KEYS[key])
                else:
                    game.move_player(*MOVE_KEYS[key])
            elif key == "e":
                game.interact()
            elif key == "r":
                game.report()
            elif key == "k":
                game.kill(game.player_id)
            elif game.test_mode and key == "x":
                game.set_role(game.player_id, "crew" if game.player.role == "impostor" else "impostor")
            elif game.test_mode and key == "n":
                game.reset_test()
            elif key in SABOTAGE_KEYS:
                trigger_sabotage(game, key)
            elif key == "v":
                if not game.apply_action(game.player_id, "vent"):
                    game.message("Vent unavailable: approach a vent; exits must be clear.")
            elif key in ("1", "2") and game.player.vent_id is not None:
                connections = game.vents.observe(game, game.player_id)["connections"]
                index = int(key) - 1
                if index < len(connections):
                    game.apply_action(game.player_id, {"kind": "vent", "target": connections[index]["id"]})
            elif key in ("[", "]"):
                game.observer_page += 1 if key == "]" else -1
            elif key == "z":
                observer = simulation or minimap_open or god_open
                current_zoom = screen_layout(game, terminal_size(), observer=observer).zoom
                game.map_zoom = 1 if current_zoom == 2 else 2
            elif key == "p":
                game.panel_visible = not game.panel_visible
            elif key == "h":
                help_open = not help_open
                if help_open:
                    minimap_open = False
            elif key == "t":
                chat_view = ChatView()
            elif key == "\t":
                minimap_open = not minimap_open
                help_open = False
            elif key == "`" and not isinstance(caps, bool) and game.god_view_allowed:
                god_open = not god_open  # Fallback where Caps Lock cannot be read.
            elif key in ("q", "escape"):
                stop_game(game)

        game.tick(min(dt, 0.15))
        if game.pending_meeting and not game.outcome:
            chat_view = None
            conduct_meeting(term, game)
            last = time.monotonic()
            next_frame = last
            continue

        if now >= next_frame:
            if chat_view is not None:
                screen = render_chat(game, chat_view)
            elif help_open:
                screen = render_help(game)
            elif simulation:
                screen = render_observer(game)
            elif minimap_open:
                screen = render_minimap(game)
            elif god_open:
                screen = render_observer(game)
            else:
                screen = render_game(game)
            if game.sabotage.kind and (chat_view is not None or help_open):
                lines = screen.splitlines()
                lines[0] = sabotage_alert(game)
                screen = fit_screen(lines)
            term.draw(screen)
            next_frame = now + 1 / game.config.fps
        sleep_for = max(0.0, min(0.01, next_frame - time.monotonic()))
        time.sleep(sleep_for)
    return game


def run(seed: Optional[int] = None, config: Optional[GameConfig] = None) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("This game requires an interactive terminal.")
        return 2

    settings = config or GameConfig()
    with Terminal() as term:
        while True:
            settings = configure_game(term, settings)
            if settings is None:
                return 0
            game = play_one(term, seed=seed, config=settings)
            while True:
                term.draw(render_end(game))
                keys = [key.lower() for key in term.read_keys()]
                if any(key == "r" for key in keys):
                    break
                if any(key in ("q", "escape", "\r", "\n") for key in keys):
                    return 0
                time.sleep(0.03)
