from __future__ import annotations
import sys
import time
from typing import Optional
from ..controllers.player import PlayerController
from ..core.config import GameConfig
from ..core.engine import TICK_STEP, Game
from ..core.models import EPSILON, MOVE_ACTIONS
from ..session import new_game
from ..core.world import MAP_H, MAP_W
from .driver import Terminal
from .keymap import DOOR_KEYS, MOVE_KEYS, SABOTAGE_KEYS
from .menu import configure_game
from .chat import ChatView, render_chat
from .text import MIN_COLUMNS, MIN_ROWS, terminal_size, fit_screen
from .palette import RED, RESET
from .ui import (render_game, render_observer, render_minimap, render_help, render_end, render_meeting,
                 render_vote_result, screen_layout, camera_origin, VOTE_KEYS, sabotage_alert)


MAX_FRAME_SECONDS = 0.15  # A stalled frame simulates at most this much; the rest is dropped.


def advance(game: Game, pending: float, dt: float) -> float:
    """Simulate whole TICK_STEP steps for the real time that passed and return what is left.

    Steps never depend on the frame rate, so the same seed and input replay the same match.
    Stops early when a meeting opens or closes, so the caller can switch screens."""
    pending += dt
    in_meeting = game.pending_meeting is not None
    while (pending + EPSILON >= TICK_STEP and not game.outcome
           and (game.pending_meeting is not None) == in_meeting):
        game.tick(TICK_STEP)
        pending -= TICK_STEP
    return pending


def trigger_sabotage(game: Game, key: str) -> bool:
    """Sabotage hotkey, for the local impostor or the simulation spectator."""
    return PlayerController().sabotage(game, SABOTAGE_KEYS[key])


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
    pending = 0.0
    last = time.monotonic()
    while game.pending_meeting and not game.outcome:
        now = time.monotonic()
        pending = advance(game, pending, min(60.0, max(0.0, now - last)))
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
            if key in ("t", "\t"):  # Tab toggles between the ballot and the chat.
                chat_view = meeting_chat
            elif not automatic and (key == "0" or key in VOTE_KEYS):
                # The ballot screen derives its status line (discussion countdown, your
                # vote) from the meeting state each frame, so nothing here can go stale.
                if not game.meetings.voting_open or game.player_id in game.meetings.votes:
                    continue
                if key == "0":
                    game.apply_action(game.player_id, "vote")
                    prompt = ""
                    continue
                index = VOTE_KEYS.index(key)
                ids = game.alive_ids()
                if index < len(ids):
                    game.apply_action(game.player_id, {"kind": "vote", "target": ids[index]})
                    prompt = ""
                else:
                    prompt = RED + "Choose one of the displayed keys." + RESET
        time.sleep(0.03)
    if game.meetings.last_result is None:
        return
    ejected, counts = game.meetings.last_result
    deadline = time.monotonic() + game.config.vote_result_seconds
    while (now := time.monotonic()) < deadline:
        term.draw(render_vote_result(game, ejected, counts, seconds_left=deadline - now))
        if term.read_keys():
            break
        time.sleep(0.03)


def check_terminal_size() -> Optional[str]:
    columns, rows = terminal_size()
    if columns < MIN_COLUMNS or rows < MIN_ROWS:
        return f"Terminal too small ({columns}x{rows}). Minimum: {MIN_COLUMNS}x{MIN_ROWS}."
    return None


def play_one(term: Terminal, seed: Optional[int] = None, config: Optional[GameConfig] = None) -> Game:
    game = new_game(seed=seed, config=config)
    player = PlayerController()
    help_open = False
    simulation = game.config.play_mode == "simulation"
    god_open = False      # Caps Lock on Windows; the ` key elsewhere.
    minimap_open = False  # Tab
    chat_view = None
    pending = 0.0  # Real time not simulated yet.
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
            if simulation and key in ("e", "r", "k", "v"):
                continue
            if key in MOVE_KEYS:
                if simulation:
                    pan_camera(game, *MOVE_ACTIONS[MOVE_KEYS[key]])
                else:
                    player.act(game, MOVE_KEYS[key])
            elif key == "e":
                player.act(game, "interact")
            elif key == "r":
                player.act(game, "report")
            elif key == "k":
                player.act(game, "kill")
            elif game.test_mode and key == "x":
                game.set_role(game.player_id, "crew" if game.player.role == "impostor" else "impostor")
            elif game.test_mode and key == "n":
                game.reset_test()
            elif key in SABOTAGE_KEYS:
                player.sabotage(game, SABOTAGE_KEYS[key])
            elif key == "v":
                if not player.act(game, "vent"):
                    game.message("Vent unavailable: approach a vent; exits must be clear.")
            elif (minimap_open and key in DOOR_KEYS and game.player.vent_id is None
                  and (simulation or game.player.role == "impostor")):
                player.close_doors(game, DOOR_KEYS[key])
            elif key in ("1", "2") and game.player.vent_id is not None:
                connections = game.vents.observe(game, game.player_id)["connections"]
                index = int(key) - 1
                if index < len(connections):
                    player.act(game, {"kind": "vent", "target": connections[index]["id"]})
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

        pending = advance(game, pending, min(dt, MAX_FRAME_SECONDS))
        player.update(game)
        if game.pending_meeting and not game.outcome:
            chat_view = None
            conduct_meeting(term, game)
            pending = 0.0
            last = time.monotonic()
            next_frame = last
            continue

        if now >= next_frame:
            if chat_view is not None:
                screen = render_chat(game, chat_view)
            elif help_open:
                screen = render_help(game)
            elif minimap_open:
                screen = render_minimap(game)
            elif simulation:
                screen = render_observer(game)
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
