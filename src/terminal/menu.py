"""Centered startup settings and Play screen."""
from __future__ import annotations
from dataclasses import replace
import time
from ..core.config import GameConfig, MENU_LIMITS
from .driver import Terminal
from .palette import BOLD, CYAN, GRAY, GREEN, RESET, WHITE, YELLOW
from .text import MIN_COLUMNS, MIN_ROWS, centered_screen, fit_line, terminal_size


MENU_ITEMS = ("play_mode", "player_count", "kill_cooldown", "tasks_per_player", "voting_seconds", "play", "quit")


def bounded_value(name: str, value: int | float) -> int | float:
    minimum, maximum = MENU_LIMITS[name]
    return max(minimum, min(maximum, value))


def normalize_settings(config: GameConfig) -> GameConfig:
    players = int(bounded_value("player_count", config.player_count))
    cooldown = bounded_value("kill_cooldown", config.kill_cooldown)
    changes = {"player_count": players,
               "impostor_count": min(config.impostor_count, (players - 1) // 2),
               "tasks_per_player": int(bounded_value("tasks_per_player", config.tasks_per_player)),
               "voting_seconds": bounded_value("voting_seconds", config.voting_seconds)}
    if cooldown != config.kill_cooldown:
        changes.update(kill_cooldown=cooldown, initial_kill_cooldown=cooldown,
                       post_meeting_kill_cooldown=cooldown)
    return replace(config, **changes)


def setting_row(label: str, value: int | float, unit: str = "") -> str:
    return f"{label:<19} < {value:g}{unit} >"


def render_setup(config: GameConfig, selected: int = 0,
                 size: tuple[int, int] | None = None) -> str:
    config = normalize_settings(config)
    mode = "Simulation" if config.play_mode == "simulation" else "Game"
    rows = [
        f"Mode                  < {mode} >",
        setting_row("Total players", config.player_count),
        setting_row("Impostor cooldown", config.kill_cooldown, "s"),
        setting_row("Tasks per crewmate", config.tasks_per_player),
        setting_row("Voting time", config.voting_seconds, "s"),
        "[ PLAY ]",
        "[ QUIT ]",
    ]
    total = (config.player_count - config.impostor_count) * config.tasks_per_player
    content = [
        BOLD + WHITE + "AMONG-ASCII / THE SKELD" + RESET,
        GRAY + "Configure your next mission" + RESET,
        "",
    ]
    for index, row in enumerate(rows):
        marker = "> " if index == selected else "  "
        color = GREEN if MENU_ITEMS[index] == "play" else YELLOW if index == selected else WHITE
        content.append((BOLD if index == selected else "") + color + marker + row + RESET)
    content += [
        "",
        f"{config.player_count} players | {config.impostor_count} impostors | {total} tasks total",
        "Simulation scaffold; no agent behavior." if config.play_mode == "simulation"
        else f"Play as {config.player_color}; other players are NPCs.",
        "",
        GRAY + "Up/Down or W/S: select | Left/Right: change" + RESET,
        GRAY + "Enter: select / Play | Q / Esc: quit" + RESET,
    ]
    inner = 54
    lines = [CYAN + "╔" + "═" * inner + "╗" + RESET]
    lines += [CYAN + "║ " + RESET + fit_line(line, inner - 2, pad=True)
              + CYAN + " ║" + RESET for line in content]
    lines.append(CYAN + "╚" + "═" * inner + "╝" + RESET)
    return centered_screen(lines, size)


def adjust_setting(config: GameConfig, selected: int, direction: int) -> GameConfig:
    config = normalize_settings(config)
    option = MENU_ITEMS[selected]
    if option == "play_mode":
        return replace(config, play_mode="simulation" if config.play_mode == "game" else "game")
    if option == "player_count":
        players = int(bounded_value(option, config.player_count + direction))
        return replace(config, player_count=players,
                       impostor_count=min(config.impostor_count, (players - 1) // 2))
    if option == "kill_cooldown":
        cooldown = round(bounded_value(option, config.kill_cooldown + direction), 2)
        return replace(config, kill_cooldown=cooldown, initial_kill_cooldown=cooldown,
                       post_meeting_kill_cooldown=cooldown)
    if option == "tasks_per_player":
        return replace(config, tasks_per_player=int(bounded_value(option, config.tasks_per_player + direction)))
    if option == "voting_seconds":
        return replace(config, voting_seconds=bounded_value(option, config.voting_seconds + 5 * direction))
    return config


def configure_game(term: Terminal, config: GameConfig) -> GameConfig | None:
    config = normalize_settings(config)
    selected = 0
    while True:
        size = terminal_size()
        term.draw(render_setup(config, selected, size))
        for key in term.read_keys():
            key = key.lower()
            if key in ("q", "escape"):
                return None
            if size[0] < MIN_COLUMNS or size[1] < MIN_ROWS:
                continue
            if key in ("up", "w"):
                selected = (selected - 1) % len(MENU_ITEMS)
            elif key in ("down", "s", "\t"):
                selected = (selected + 1) % len(MENU_ITEMS)
            elif key in ("left", "a", "-", "right", "d", "+", "="):
                config = adjust_setting(config, selected, -1 if key in ("left", "a", "-") else 1)
            elif key in ("\r", "\n", " "):
                if MENU_ITEMS[selected] == "play":
                    return config
                if MENU_ITEMS[selected] == "quit":
                    return None
                config = adjust_setting(config, selected, 1)
        time.sleep(0.03)
