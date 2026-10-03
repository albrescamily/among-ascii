"""Read-only game views; screen state lives on the local game controller."""
from __future__ import annotations
import math
import textwrap
from dataclasses import dataclass
from typing import Optional
from ..core.engine import Game
from ..core.models import Player, Body, Pos
from .palette import *
from ..core.world import CONTAINERS, MAP_W, MAP_H, ROOM_LABELS, DOOR_CELLS, EMERGENCY_POS, manhattan, room_at
from ..core.tasks import TASK_NAMES
from .text import (ANSI_SGR, MIN_COLUMNS, MIN_ROWS, terminal_size, display_width,
                   fit_line, fit_screen, centered_screen)

@dataclass(frozen=True)
class ScreenLayout:
    width: int
    height: int
    panel_width: int
    map_space: int
    zoom: int
    map_columns: int
    map_rows: int


def screen_layout(game: Game, size: tuple[int, int], observer: bool = False) -> ScreenLayout:
    columns, rows = size
    width = max(1, columns - 1)
    height = max(1, rows - 8)
    panel_width = (32 if columns >= 88 else 26) if game.panel_visible else 0
    map_space = max(1, width - 2 - (panel_width + 1 if panel_width else 0))
    auto_zoom = 2 if map_space >= (MAP_W * 2 if observer else 50) else 1
    zoom = game.map_zoom or auto_zoom
    return ScreenLayout(width, height, panel_width, map_space, zoom,
                        max(1, min(MAP_W, map_space // zoom)), min(MAP_H, height))


def camera_origin(game: Game, layout: ScreenLayout) -> Pos:
    center = game.camera_pos if game.config.play_mode == "simulation" else game.player_pos
    return (max(0, min(center[0] - layout.map_columns // 2, MAP_W - layout.map_columns)),
            max(0, min(center[1] - layout.map_rows // 2, MAP_H - layout.map_rows)))


def align_status(left: str, right: str, width: int) -> str:
    right_width = min(display_width(right), width)
    left_width = max(0, width - right_width - 1)
    return fit_line(left, left_width, pad=True) + " " + fit_line(right, right_width)


def panel_heading(title: str, width: int) -> str:
    return CYAN + " " + title + " " + "─" * max(0, width - len(title) - 2) + RESET


def expand_map_cell(cell: str, zoom: int) -> str:
    """Give map tiles a square aspect ratio without duplicating entity symbols."""
    if zoom == 1 or display_width(cell) == zoom:
        return cell
    glyph = ANSI_SGR.sub("", cell)
    return cell * zoom if glyph in ("#", "┄") else cell + " " * (zoom - 1)


def container_cell(pos: Pos, color: str, zoom: int = 1) -> str:
    """Hollow cargo casing with rounded corners, end straps and a shaded rim."""
    x, y = pos
    left, top, right, bottom = next(bounds for bounds in CONTAINERS.values()
                                    if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])
    width = (right - left + 1) * zoom
    if y == top:
        row = list("╭" + "─" * (width - 2) + "╮")
    elif y == bottom:
        row = list("╰" + "─" * (width - 2) + "╯")
    else:
        row = list("│" + " " * (width - 2) + "│")
    # Keep narrow 1X sprites simple; show the reinforcing straps when they fit.
    straps = (2, width - 3) if width >= 10 else ()
    for column in straps:
        row[column] = "┬" if y == top else "┴" if y == bottom else "│"
    offset = (x - left) * zoom
    shade = DARK if color == DARK else STEEL_SHADE
    result = []
    for column in range(offset, offset + zoom):
        ink = shade if y == bottom or column == width - 1 or column in straps else color
        result.append(ink + row[column])
    return "".join(result) + RESET


def render_map_layout(game: Game, panel: list[str], footer: list[str],
                      visible: Optional[set[Pos]] = None,
                      size: Optional[tuple[int, int]] = None) -> str:
    size = size or terminal_size()
    if size[0] < MIN_COLUMNS or size[1] < MIN_ROWS:
        return fit_screen([], size)
    layout = screen_layout(game, size, observer=visible is None)
    x0, y0 = camera_origin(game, layout)
    full_map = layout.map_columns == MAP_W and layout.map_rows == MAP_H
    view = "FULL MAP" if full_map else "CAMERA"
    mode = ("SIMULATION" if game.config.play_mode == "simulation" else "DIRECTOR VIEW") if visible is None else f"{game.player.name.upper()} / {game.player.role.upper()}"
    color = RED if visible is None else CYAN
    clock = f"{int(game.elapsed // 60):02d}:{int(game.elapsed % 60):02d}"
    tasks = f"TASKS {len(game.completed_tasks)}/{len(game.assigned_tasks)}"
    position_status = f" {CYAN}{room_at(game.player_pos)}{RESET}  {GRAY}@ {game.player_pos[0]},{game.player_pos[1]}{RESET}"
    if game.config.play_mode == "simulation":
        position_status = f" {sum(actor.alive for actor in game.players)}/{len(game.players)} PLAYERS ALIVE"
        progress = game.tasks.crew_progress(game)
        tasks = f"TASKS {progress['completed']}/{progress['total']}"
    lines = [
        align_status(BOLD + color + " AMONG-ASCII / THE SKELD" + RESET,
                     f"{color}{mode}{RESET}  {clock} ", layout.width),
        align_status(position_status,
                     f"{tasks}  |  {layout.zoom}X {view} ", layout.width),
        crew_task_bar(game, layout.width),
    ]
    map_title = f" {view} / {'LIVE' if visible is None else 'FIELD OF VIEW'} "
    top = "┌" + map_title[:layout.map_space].ljust(layout.map_space, "─")
    if layout.panel_width:
        title = " CREW STATUS " if visible is None else " MISSION CONTROL "
        top += "┬" + title + "─" * max(0, layout.panel_width - len(title))
    lines.append(BLUE + top + "┐" + RESET)
    x_padding = (layout.map_space - layout.map_columns * layout.zoom) // 2
    y_padding = (layout.height - layout.map_rows) // 2
    for row in range(layout.height):
        cells: list[str] = []
        if y_padding <= row < y_padding + layout.map_rows:
            for x in range(x0, x0 + layout.map_columns):
                pos = (x, y0 + row - y_padding)
                cell = (observer_map_cell(game, pos, zoom=layout.zoom) if visible is None
                        else map_cell(game, pos, visible, zoom=layout.zoom))
                cells.append(expand_map_cell(cell, layout.zoom))
        map_row = fit_line(" " * x_padding + "".join(cells), layout.map_space, pad=True)
        line = BLUE + "│" + RESET + map_row
        if layout.panel_width:
            detail = panel[row] if row < len(panel) else ""
            line += BLUE + "│" + RESET + fit_line(detail, layout.panel_width, pad=True)
        lines.append(line + BLUE + "│" + RESET)
    bottom = "└" + "─" * layout.map_space
    if layout.panel_width:
        bottom += "┴" + "─" * layout.panel_width
    lines.append(BLUE + bottom + "┘" + RESET)
    lines.extend(footer)
    return fit_screen(lines, size)


def progress_bar(value: float, width: int = 25) -> str:
    value = max(0.0, min(1.0, value))
    filled = int(round(value * width))
    return GREEN + "█" * filled + DARK + "░" * (width - filled) + RESET


def crew_task_bar(game: Game, width: int) -> str:
    progress = game.tasks.crew_progress(game)
    label = f" CREW TASKS {progress['completed']}/{progress['total']} "
    percent = f" {progress['fraction']:.0%} "
    bar_width = max(1, width - len(label) - len(percent))
    return BOLD + CYAN + label + RESET + progress_bar(progress["fraction"], bar_width) + WHITE + percent + RESET


def map_cell(game: Game, pos: Pos, visible: set[Pos], *, zoom: int = 1) -> str:
    x, y = pos
    in_view = pos in visible
    known = pos in game.discovered
    if not known:
        return " "

    if in_view and game.player_alive and game.player.vent_id is None and pos == game.player_pos:
        symbol = game.player.symbol if game.config.play_mode == "simulation" else "@"
        return BOLD + game.player.color + symbol + RESET

    if in_view:
        for npc in game.players[1:]:
            if npc.alive and npc.vent_id is None and npc.pos == pos:
                return BOLD + npc.color + npc.symbol + RESET
        for body in game.bodies:
            if body.pos == pos:
                return BOLD + RED + "†" + RESET

    tile = game.grid[y][x]
    if tile == " ":
        return " "
    if not in_view:
        if tile == "D":
            return DARK + DOOR_CELLS[pos].glyph + RESET
        if tile == "O":
            return container_cell(pos, DARK, zoom)
        return DARK + ("#" if tile == "#" else "·") + RESET
    if tile == "#":
        return BLUE + "#" + RESET
    if tile == "O":
        return container_cell(pos, STEEL, zoom)
    if tile == "D":
        return CYAN + DOOR_CELLS[pos].glyph + RESET
    if tile == "T":
        if pos in game.assigned_tasks and pos not in game.completed_tasks:
            return BOLD + YELLOW + "◆" + RESET
        if pos in game.completed_tasks:
            return GREEN + "◇" + RESET
        return DIM + GRAY + "·" + RESET
    if tile == "E":
        return BOLD + RED + "◉" + RESET
    if tile == "V":
        return MAGENTA + "▣" + RESET
    if pos in ROOM_LABELS:
        return WHITE + ROOM_LABELS[pos] + RESET
    return DIM + GRAY + "·" + RESET


def render_game(game: Game, size: Optional[tuple[int, int]] = None) -> str:
    size = size or terminal_size()
    layout = screen_layout(game, size)
    width = layout.panel_width or 32
    visible = game.visible_positions()
    task_ratio = len(game.completed_tasks) / max(1, len(game.assigned_tasks))
    panel = [
        align_status(BOLD + WHITE + " TASK PROGRESS" + RESET, f"{task_ratio:.0%} ", width),
        " " + progress_bar(task_ratio, width - 2),
        f" {len(game.completed_tasks)} of {len(game.assigned_tasks)} tasks complete",
        "",
    ]

    if game.active_task:
        name = TASK_NAMES[game.active_task]
        panel += [
            BOLD + YELLOW + f" SYNCING... {game.task_progress:.0%}" + RESET,
            " " + progress_bar(game.task_progress, width - 2),
            *[" " + line for line in textwrap.wrap(name, width - 2)],
            " " + CYAN + room_at(game.active_task) + RESET,
        ]
    else:
        remaining = [p for p in game.assigned_tasks if p not in game.completed_tasks]
        panel.append(panel_heading("ASSIGNMENTS", width))
        for index, pos in enumerate(remaining[:5], start=1):
            marker = "!" if manhattan(game.player_pos, pos) <= 1 else str(index)
            panel.append(f" {YELLOW}{marker}{RESET} {TASK_NAMES[pos]}")
            if layout.height >= 23:
                panel.append("   " + CYAN + room_at(pos) + RESET)

    if game.player_alive:
        if (any(actor.alive and actor.vent_id is None and actor.pos in visible and manhattan(game.player_pos, actor.pos) <= 3
                for actor in game.npcs) and len(panel) < layout.height - 3):
            panel.append(RED + BOLD + " MOVEMENT NEARBY..." + RESET)

    if len(panel) < layout.height - 3:
        panel.append("")
    panel.append(panel_heading("RECENT EVENTS", width))
    for message in list(game.messages)[:4]:
        wrapped = textwrap.wrap(message, width - 3)
        for index, line in enumerate(wrapped):
            if len(panel) >= layout.height:
                break
            panel.append((" › " if index == 0 else "   ") + line)

    if game.player.vent_id is not None:
        hint = MAGENTA + " Hidden | kill cooldown paused" + RESET
    elif game.active_task:
        hint = YELLOW + " Stay nearby until syncing finishes." + RESET
    elif any(manhattan(game.player_pos, p) <= 1 for p in game.assigned_tasks if p not in game.completed_tasks):
        hint = YELLOW + " Press E to start the nearby task." + RESET
    elif any(manhattan(game.player_pos, b.pos) <= 1 for b in game.bodies):
        hint = RED + BOLD + " BODY FOUND — press R to report." + RESET
    elif manhattan(game.player_pos, EMERGENCY_POS) <= 1 and game.emergency_available:
        hint = RED + " E: call an emergency meeting." + RESET
    else:
        hint = DIM + " ◆ task   ◉ meeting   † body   · floor" + RESET
    footer = [
        vent_hint(game) or " WASD move E use R report V vent TAB view Z zoom P panel",
        align_status(hint, "T chat  H help  Q quit ", layout.width),
    ]
    return render_map_layout(game, panel, footer, visible, size)


def vent_hint(game: Game) -> str:
    if game.config.play_mode == "simulation" or not game.player_alive or game.player.role != "impostor":
        return ""
    state = game.vents.observe(game, game.player_id)
    if state["current"]:
        choices = "  ".join(f"{index} {item['room']}" for index, item in enumerate(state["connections"], 1))
        return " IN VENT | V exit | " + choices
    if state["nearby"]:
        return " V enter vent | K kill | WASD move | E use | R report"
    return ""


def observer_map_cell(game: Game, pos: Pos, *, zoom: int = 1) -> str:
    """Render one omniscient cell for the live observer/debug screen."""
    x, y = pos
    if game.player_alive and game.player_pos == pos:
        symbol = game.player.symbol if game.config.play_mode == "simulation" else "@"
        return BOLD + game.player.color + symbol + RESET

    for npc in game.players[1:]:
        if npc.alive and npc.pos == pos:
            return BOLD + npc.color + npc.symbol + RESET
    for body in game.bodies:
        if body.pos == pos:
            return BOLD + RED + "†" + RESET

    tile = game.grid[y][x]
    if tile == " ":
        return " "
    if tile == "#":
        return BLUE + "#" + RESET
    if tile == "O":
        return container_cell(pos, STEEL, zoom)
    if tile == "D":
        return CYAN + DOOR_CELLS[pos].glyph + RESET
    if tile == "T":
        if pos in game.completed_tasks:
            return GREEN + "◇" + RESET
        if pos in game.assigned_tasks:
            return BOLD + YELLOW + "◆" + RESET
        return GRAY + "◇" + RESET
    if tile == "E":
        return BOLD + RED + "◉" + RESET
    if tile == "V":
        return MAGENTA + "▣" + RESET
    if pos in ROOM_LABELS:
        return WHITE + ROOM_LABELS[pos] + RESET
    return DIM + GRAY + "·" + RESET


def npc_activity(npc: Player) -> str:
    if not npc.alive:
        return "DEAD"
    if npc.vent_id is not None:
        return "IN VENT"
    if npc.role == "impostor":
        return "HUNTING" if npc.goal is not None else "BLENDING IN"
    return "EN ROUTE" if npc.path else "SCANNING"


def render_observer(game: Game, size: Optional[tuple[int, int]] = None) -> str:
    """Omniscient live monitor: full map plus every entity's internal state."""
    size = size or terminal_size()
    layout = screen_layout(game, size, observer=True)
    width = layout.panel_width or 32
    available = max(1, layout.height - 5)
    detail_rows = 3 if available >= len(game.players) * 3 else 2 if available >= len(game.players) * 2 else 1
    page_size = max(1, available // detail_rows)
    pages = (len(game.players) + page_size - 1) // page_size
    page = game.observer_page % pages
    first = page * page_size
    panel = [panel_heading(f"CREW MANIFEST {page + 1}/{pages}", width)]

    for npc in game.players[first:first + page_size]:
        role = "IMPOSTOR" if npc.role == "impostor" else "CREW"
        role_color = RED if npc.role == "impostor" else npc.color
        state = ("VENT" if npc.vent_id is not None else "ALIVE") if npc.alive else "DEAD"
        symbol = "@" if npc.id == game.player_id and game.config.play_mode == "game" else npc.symbol
        panel.append(f" {npc.color}{symbol} {npc.name:<6}{RESET} {role_color}{role:<8}{RESET} {state}")
        if detail_rows >= 2:
            if game.config.play_mode == "simulation":
                controller = "NO CONTROLLER"
                panel.append("   " + controller)
            else:
                panel.append(f"   {room_at(npc.pos)} {npc.pos!s}")
        if detail_rows >= 3:
            show_goal = game.config.play_mode == "game"
            goal = f" → {npc.goal}" if npc.alive and npc.goal and show_goal else ""
            activity = "INACTIVE" if game.config.play_mode == "simulation" else npc_activity(npc)
            panel.append(f"   {GRAY}{activity}{goal}{RESET}")

    panel += [
        panel_heading("SHIP STATUS", width),
        f" bodies {len(game.bodies)} | kill CD {game.kill_cooldown:04.1f}s",
        f" tasks {game.tasks.crew_progress(game)['completed']}/{game.tasks.crew_progress(game)['total']}",
        f" meeting {'PENDING' if game.pending_meeting else 'none'}",
    ]
    footer = [
        " WASD/arrows camera  Z zoom  P panel" if game.config.play_mode == "simulation"
        else vent_hint(game) or " WASD move E use R report V vent TAB back Z zoom P panel",
        align_status(" " + (f"{game.chat.messages[-1].color}: {game.chat.messages[-1].text}"
                            if game.chat.messages else "[ / ] crew pages   ┆/┄ open doors"),
                     "T chat  H help  Q quit ", layout.width),
    ]
    return render_map_layout(game, panel, footer, size=size)


def render_help(game: Game) -> str:
    content = [
        "HOW TO PLAY // THE SKELD",
        "The world keeps running on this screen.",
        "",
        "WASD / arrows move",
        "E             use / interact",
        "R             report a body; K to kill",
        "V             vent in/out; 1/2 travel inside",
        "Tab           map monitor",
        "Z             toggle map zoom (1X / 2X)",
        "P             show / hide side panel",
        "[ / ]         previous / next crew page",
        "T             broadcast chat (Esc: back)",
        "H             close help",
        "Q             quit game",
        "",
        f"Finish {game.config.tasks_per_player} tasks or eject impostors.",
        f"Full map + panel: at least {MAP_W + 36}x{MAP_H + 8}.",
        "Smaller windows: the camera follows @.",
    ]
    if game.config.play_mode == "simulation":
        content = ["SIMULATION // THE SKELD", "Scaffold without agent behavior.", "",
                   "WASD / arrows move camera",
                   "Z             toggle map zoom (1X / 2X)",
                   "P             show / hide side panel",
                   "[ / ]         previous / next crew page",
                   "T             broadcast chat (read only)",
                   "H             close help", "Q             stop simulation", "",
                   "No movement, tasks, chat or votes by agents.",
                   "Game NPCs run only in Game mode.",
                   f"Full map + panel: at least {MAP_W + 36}x{MAP_H + 8}.",
                   "R on the final screen returns to setup."]
    box = [CYAN + "╔" + "═" * 44 + "╗" + RESET]
    box += ["║ " + fit_line(line, 42, pad=True) + " ║" for line in content]
    box.append(CYAN + "╚" + "═" * 44 + "╝" + RESET)
    return fit_screen(box)


VOTE_KEYS = "123456789abc"


def render_meeting(game: Game, reporter_id: str, body: Optional[Body], prompt: str = "",
                   size: Optional[tuple[int, int]] = None) -> str:
    size = size or terminal_size()
    content = [(BOLD + game.entity(body.victim_id).color + body.victim_name + RESET + " body found.")
               if body else "No body was reported."]
    content += [BOLD + "Who should be ejected? (1-9 / A-C)" + RESET]
    choices = []
    for key, entity_id in zip(VOTE_KEYS, game.alive_ids()):
        actor = game.entity(entity_id)
        suffix = " @" if entity_id == game.player_id and game.config.play_mode == "game" else ""
        voted = entity_id in game.meetings.votes
        status = GREEN + "VOTED" if voted else YELLOW + "WAITING"
        choices.append(f" {BOLD}[{key.upper()}]{RESET} {actor.color}{actor.name + suffix:<8}{RESET}"
                       f"  {status}{RESET}")
    for index in range(0, len(choices), 2):
        content.append(fit_line(choices[index], 26, pad=True)
                       + (choices[index + 1] if index + 1 < len(choices) else ""))
    content.append(f"{BOLD}[0]{RESET} Skip vote | No vote at timeout = Skip")
    if prompt:
        content.append(prompt)
    content.append("World paused | T chat | Q / Esc quit")
    title = f" EMERGENCY MEETING | {math.ceil(game.meetings.remaining)}s "
    lines = [BOLD + RED + "╔" + title.center(55, "═") + "╗" + RESET]
    lines += [RED + "║ " + RESET + fit_line(line, 53, pad=True) + RED + " ║" + RESET
              for line in content]
    lines.append(RED + "╚" + "═" * 55 + "╝" + RESET)
    return centered_screen(lines, size)


def render_vote_result(
    game: Game,
    ejected: Optional[str],
    counts: dict[Optional[str], int],
) -> str:
    lines = [BOLD + CYAN + "╔" + " VOTING RESULTS ".center(54, "═") + "╗" + RESET, ""]
    for entity_id, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
        name = "Skip" if entity_id is None else game.name_of(entity_id)
        lines.append(f"  {name:<16} {YELLOW}{'●' * count}{RESET}  {count}")
    lines.append("")
    if ejected is None:
        lines.append(BOLD + "  Tie or skip majority. Nobody was ejected." + RESET)
    else:
        role_text = ""
        if ejected != game.player_id:
            role_text = " They were the impostor." if game.npc(ejected).role == "impostor" else " They were not the impostor."
        actor = game.entity(ejected)
        lines.append("  " + BOLD + actor.color + actor.name + RESET
                     + BOLD + f" was ejected.{role_text}" + RESET)
    lines += ["", DIM + ("  Simulation resumes automatically." if game.config.play_mode == "simulation"
                          else "  Press any key to continue.") + RESET]
    lines.append(CYAN + "╚" + "═" * 54 + "╝" + RESET)
    return centered_screen(lines)


def render_end(game: Game) -> str:
    won = game.outcome == "victory"
    color = GREEN if won else RED
    title = "MISSION COMPLETE" if won else "MISSION FAILED"
    if game.config.play_mode == "simulation":
        title = "CREW WINS" if game.winner == "crew" else "IMPOSTORS WIN" if game.winner == "impostor" else "SIMULATION ENDED"
        color = GREEN if game.winner == "crew" else RED if game.winner == "impostor" else CYAN
    elif game.outcome == "timeout":
        title, color = "TIME LIMIT REACHED", CYAN
    impostors = ", ".join(game.name_of(actor_id) for actor_id in game.impostor_ids) or "none"
    return centered_screen([
        color + BOLD + "╔══════════════════════════════════════════════════════╗" + RESET,
        color + BOLD + f"║ {title:^52} ║" + RESET,
        color + BOLD + "╚══════════════════════════════════════════════════════╝" + RESET,
        "",
        "  " + game.outcome_reason,
        f"  Impostors: {impostors}.",
        f"  Tasks: {len(game.completed_tasks)}/{len(game.assigned_tasks)}",
        f"  Time: {int(game.elapsed // 60):02d}:{int(game.elapsed % 60):02d}",
        "",
        f"  {BOLD}[R]{RESET} setup / play again    {BOLD}[Q/Enter]{RESET} quit",
    ])
