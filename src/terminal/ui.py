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
from ..core.sabotage import TITLES
from ..core.doors import DOOR_KEYS, DOOR_ROOMS, room_doors
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


def panel_heading(title: str, width: int, color: str = CYAN) -> str:
    return color + BOLD + " " + title + RESET + color + " " + "─" * max(0, width - len(title) - 2) + RESET


def keys_line(pairs: list[tuple[str, str]]) -> str:
    """`KEY action` hints with the key highlighted, used by panels and footers."""
    return "  ".join(f"{BOLD}{WHITE}{key}{RESET} {GRAY}{label}{RESET}" for key, label in pairs)


def expand_map_cell(cell: str, zoom: int) -> str:
    """Give map tiles a square aspect ratio without duplicating entity symbols."""
    if zoom == 1 or display_width(cell) == zoom:
        return cell
    glyph = ANSI_SGR.sub("", cell)
    return cell * zoom if glyph in ("#", "-", "+") else cell + " " * (zoom - 1)


def container_cell(pos: Pos, color: str, zoom: int = 1) -> str:
    """Use the ship's wall tiles for the rim, leaving the interior undrawn."""
    x, y = pos
    left, top, right, bottom = next(bounds for bounds in CONTAINERS.values()
                                    if bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])
    glyph = "#" if x in (left, right) or y in (top, bottom) else " "
    return color + glyph * zoom + RESET


OPEN_DOOR, CLOSED_DOOR = "-", "+"


def door_cell(game: Game, pos: Pos, color: str) -> Optional[str]:
    """Doorway glyph: `-` open in `color`, `+` closed in bold red; None if not a door."""
    tile = game.grid[pos[1]][pos[0]]
    if tile == "C":
        return BOLD + RED + CLOSED_DOOR + RESET
    if tile == "D":
        return color + OPEN_DOOR + RESET
    return None


def sabotage_alert(game: Game) -> str:
    system = game.sabotage
    instruction = "Repair ! panels" if game.config.play_mode == "simulation" else "E: repair ! panels"
    if not system.critical:
        return BOLD + RED + f" ! {TITLES[system.kind]} | crew vision reduced | " + instruction + RESET
    return (BOLD + RED + f" ! {TITLES[system.kind]} | {math.ceil(max(0, system.remaining - 1e-9)):02d}s"
            + " | " + instruction + RESET)


def sabotage_panel(game: Game, width: int) -> list[str]:
    """Live emergency status, shown in every view while a sabotage lasts."""
    system = game.sabotage
    if not system.kind:
        return []
    status = (f"{math.ceil(max(0, system.remaining - 1e-9))}s left" if system.critical
              else "vision reduced")
    lines = [panel_heading("CRITICAL EMERGENCY" if system.critical else "LIGHTS OUT", width, RED),
             " " + BOLD + RED + TITLES[system.kind] + RESET + GRAY + " | " + RESET + YELLOW + status + RESET]
    for index, panel in enumerate(system.panels):
        progress = 1.0 if index in system.completed else system.progress.get(index, 0.0)
        if index in system.completed:
            state = GREEN + BOLD + "OK" + RESET
        elif system.kind == "reactor" and index in system.workers.values() and not progress:
            state = YELLOW + "HOLDING" + RESET
        else:
            state = f"{progress:.0%}"
        name = f" {BOLD}{YELLOW}!{RESET} {panel.name} {GRAY}@{panel.pos[0]},{panel.pos[1]}{RESET}"
        lines += [align_status(name, state + " ", width), "   " + progress_bar(progress, width - 4)]
    if system.kind == "reactor":
        lines.append(GRAY + " Two players, one per panel." + RESET)
    lines.append(GRAY + (" Awaiting crew repairs." if game.config.play_mode == "simulation"
                         else f" E at ! and hold {game.config.sabotage_repair_seconds:g}s still.") + RESET)
    return lines + [""]


def sabotage_controls(game: Game, width: int) -> list[str]:
    """Trigger keys and cooldown, for impostors and the simulation spectator."""
    if not game.config.sabotage_enabled or game.sabotage.kind:
        return []
    return [align_status(" Sabotage", cooldown_text(game.sabotage.cooldown) + " ", width),
            "   " + keys_line([("F", "Reactor"), ("G", "O2")]),
            "   " + keys_line([("J", "Admin"), ("L", "Lights")])]


def test_panel(game: Game, width: int) -> list[str]:
    if not game.test_mode:
        return []
    return [panel_heading("TEST MODE", width, MAGENTA),
            " " + keys_line([("X", "swap role"), ("N", "reset round")]), ""]


def sabotage_marker(game: Game, pos: Pos) -> str | None:
    for index, panel in enumerate(game.sabotage.panels):
        if panel.pos == pos:
            return BOLD + (GREEN if index in game.sabotage.completed else YELLOW) + "!" + RESET
    return None


def alarm_terrain(game: Game, pos: Pos, zoom: int, in_view: bool = True) -> str | None:
    if not game.sabotage.kind:
        return None
    tile = game.grid[pos[1]][pos[0]]
    color = RED if in_view else DIM + RED
    if tile == "O":
        return container_cell(pos, color, zoom)
    glyph = {"#": "#", "D": OPEN_DOOR, "C": CLOSED_DOOR,
             "T": "◇", "V": "▣", "E": "◉", " ": " "}.get(tile, ROOM_LABELS.get(pos, "·" if in_view else " "))
    return color + glyph + RESET


EMERGENCY_LEGEND = BOLD + RED + "◉" + RESET + GRAY + " emergency button" + RESET


def clock_text(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def cooldown_text(seconds: float) -> str:
    return GREEN + BOLD + "READY" + RESET if seconds <= 1e-9 else YELLOW + f"{math.ceil(seconds - 1e-9)}s" + RESET


VIEW_NAMES = {"player": "PLAYER VIEW", "god": "GOD VIEW", "minimap": "MINI MAP"}
VIEW_COLORS = {"player": CYAN, "god": YELLOW, "minimap": GREEN}


def mode_label(game: Game, view_mode: str) -> str:
    """Play mode, plus which view is open; Simulation only has the spectator view."""
    if game.config.play_mode == "simulation":
        return "SIMULATION / MINI MAP" if view_mode == "minimap" else "SIMULATION"
    mode = "TEST" if game.test_mode else "GAME"
    return f"{mode} / {VIEW_NAMES[view_mode]}"


def player_identity(game: Game) -> str:
    """Local player's color and role, e.g. `CYAN / CREW`; marks ghosts."""
    role_color = RED if game.player.role == "impostor" else CYAN
    ghost = GRAY + " (ghost)" + RESET if not game.player_alive else ""
    return (BOLD + game.player.color + game.player.name.upper() + RESET + GRAY + " / " + RESET
            + BOLD + role_color + game.player.role.upper() + RESET + ghost)


def hud_row(fields: list[tuple[str, str]], width: int) -> str:
    """`LABEL value` columns: the first gets the room it needs, the rest share the width evenly."""
    cells = [f" {GRAY}{label}{RESET} {value}" for label, value in fields]
    first = min(width // 2, max(width // len(cells), display_width(cells[0]) + 3))
    column = max(1, (width - first) // max(1, len(cells) - 1))
    return fit_line(fit_line(cells[0], first, pad=True)
                    + "".join(fit_line(cell, column, pad=True) for cell in cells[1:]), width, pad=True)


def hud_lines(game: Game, layout: ScreenLayout, view: str, view_mode: str) -> list[str]:
    """Top HUD: title + mode, LOCATION / TIME / ALIVE / TASKS / VIEW, then the task bar or alarm."""
    color = VIEW_COLORS[view_mode]
    if game.config.play_mode == "simulation":
        location = f"{room_at(game.camera_pos)} {GRAY}(camera){RESET}"
        progress = game.tasks.crew_progress(game)
        tasks = f"{progress['completed']}/{progress['total']}"
    else:
        x, y = game.player_pos
        location = f"{room_at(game.player_pos)} {GRAY}@{x},{y}{RESET}"
        # Impostors have no real tasks of their own.
        tasks = (f"{len(game.completed_tasks)}/{len(game.assigned_tasks)}"
                 if game.player.role == "crew" else "-")
    alive = f"{sum(actor.alive for actor in game.players)}/{len(game.players)}"
    status = f"{GRAY}MODE{RESET} {color}{mode_label(game, view_mode)}{RESET} "
    fields = [("LOCATION", location), ("TIME", clock_text(game.elapsed)), ("ALIVE", alive), ("TASKS", tasks)]
    if game.config.play_mode != "simulation":
        status = f"{GRAY}PLAYER{RESET} {player_identity(game)}   " + status
        left = game.player.emergencies_left
        fields.append(("BUTTONS", str(left) if left else GRAY + "0" + RESET))
    fields.append(("VIEW", f"{layout.zoom}X {view}"))
    return [
        align_status(BOLD + color + " AMONG-ASCII / THE SKELD" + RESET, status, layout.width),
        hud_row(fields, layout.width),
        sabotage_alert(game) if game.sabotage.kind else crew_task_bar(game, layout.width),
    ]


MAP_TITLES = {"player": "FIELD OF VIEW", "god": "LIVE", "minimap": "MINI MAP"}
PANEL_TITLES = {"player": " MISSION CONTROL ", "god": " CREW STATUS ", "minimap": " MISSION CONTROL "}


def render_map_layout(game: Game, panel: list[str], footer: list[str],
                      visible: Optional[set[Pos]] = None,
                      size: Optional[tuple[int, int]] = None,
                      view_mode: Optional[str] = None) -> str:
    size = size or terminal_size()
    if size[0] < MIN_COLUMNS or size[1] < MIN_ROWS:
        return fit_screen([], size)
    view_mode = view_mode or ("god" if visible is None else "player")
    layout = screen_layout(game, size, observer=view_mode != "player")
    border = RED if game.sabotage.kind else YELLOW if view_mode == "god" else BLUE
    x0, y0 = camera_origin(game, layout)
    full_map = layout.map_columns == MAP_W and layout.map_rows == MAP_H
    view = "FULL MAP" if full_map else "CAMERA"
    lines = hud_lines(game, layout, view, view_mode)
    map_title = f" {view} / {MAP_TITLES[view_mode]} "
    top = "┌" + map_title[:layout.map_space].ljust(layout.map_space, "─")
    if layout.panel_width:
        title = PANEL_TITLES[view_mode]
        top += "┬" + title + "─" * max(0, layout.panel_width - len(title))
    lines.append(border + top + "┐" + RESET)
    x_padding = (layout.map_space - layout.map_columns * layout.zoom) // 2
    y_padding = (layout.height - layout.map_rows) // 2
    for row in range(layout.height):
        cells: list[str] = []
        if y_padding <= row < y_padding + layout.map_rows:
            for x in range(x0, x0 + layout.map_columns):
                pos = (x, y0 + row - y_padding)
                if view_mode == "god":
                    cell = observer_map_cell(game, pos, zoom=layout.zoom)
                elif view_mode == "minimap":
                    cell = minimap_cell(game, pos, zoom=layout.zoom)
                else:
                    cell = map_cell(game, pos, visible, zoom=layout.zoom)
                cells.append(expand_map_cell(cell, layout.zoom))
        map_row = fit_line(" " * x_padding + "".join(cells), layout.map_space, pad=True)
        line = border + "│" + RESET + map_row
        if layout.panel_width:
            detail = panel[row] if row < len(panel) else ""
            line += border + "│" + RESET + fit_line(detail, layout.panel_width, pad=True)
        lines.append(line + border + "│" + RESET)
    bottom = "└" + "─" * layout.map_space
    if layout.panel_width:
        bottom += "┴" + "─" * layout.panel_width
    lines.append(border + bottom + "┘" + RESET)
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


def own_tasks(game: Game) -> list[Pos]:
    """Tasks the local player should see; impostors have none to do."""
    return game.assigned_tasks if game.player.role == "crew" else []


def impostor_panel(game: Game, width: int, visible: set[Pos]) -> list[str]:
    actor = game.player
    targets = [other for other in game.players if other.alive and other.role == "crew"
               and other.vent_id is None and other.pos in visible]
    in_range = any(manhattan(actor.pos, other.pos) <= game.config.kill_radius for other in targets)
    if actor.vent_id is not None:
        vent = MAGENTA + "hidden inside" + RESET
    elif game.vents.observe(game, game.player_id)["nearby"]:
        vent = CYAN + "V to enter" + RESET
    else:
        vent = GRAY + "none nearby" + RESET
    sight = f"{len(targets)} in sight" + (RED + BOLD + "  IN RANGE" + RESET if in_range else "")
    return [panel_heading("IMPOSTOR", width, RED),
            BOLD + RED + " Kill the crewmates." + RESET, "",
            align_status(" Kill: " + cooldown_text(actor.kill_clock), GRAY + "K " + RESET, width),
            " Crew: " + sight,
            " Vent: " + vent,
            *sabotage_controls(game, width),
            GRAY + " Blend in: fake tasks." + RESET, ""]


def crew_panel(game: Game, layout: ScreenLayout, width: int) -> list[str]:
    done, total = len(game.completed_tasks), len(game.assigned_tasks)
    ratio = done / max(1, total)
    panel = [panel_heading("CREWMATE", width),
             BOLD + CYAN + " Do your tasks." + RESET, "",
             panel_heading("TASK PROGRESS", width),
             align_status(f" {done} of {total} complete", f"{ratio:.0%} ", width),
             " " + progress_bar(ratio, width - 2), ""]
    if game.active_task:
        return panel + [
            BOLD + YELLOW + f" SYNCING... {game.task_progress:.0%}" + RESET,
            " " + progress_bar(game.task_progress, width - 2),
            *[" " + line for line in textwrap.wrap(TASK_NAMES[game.active_task], width - 2)],
            " " + CYAN + room_at(game.active_task) + RESET, ""]
    remaining = sorted((pos for pos in game.assigned_tasks if pos not in game.completed_tasks),
                       key=lambda pos: manhattan(game.player_pos, pos))
    panel.append(panel_heading("ASSIGNMENTS", width))
    if not remaining:
        panel.append(GREEN + BOLD + " All tasks done!" + RESET)
    shown = 5 if layout.height >= 23 else 3
    for index, pos in enumerate(remaining[:shown], start=1):
        near = manhattan(game.player_pos, pos) <= 1
        marker = BOLD + YELLOW + "!" if near else GRAY + str(index)
        panel.append(f" {marker}{RESET} {TASK_NAMES[pos]}")
        if layout.height >= 23:
            where = YELLOW + "here: press E" + RESET if near else GRAY + f"~{manhattan(game.player_pos, pos)} steps" + RESET
            panel.append(align_status("   " + CYAN + room_at(pos) + RESET, where + " ", width))
    if len(remaining) > shown:
        panel.append(GRAY + f"   +{len(remaining) - shown} more (nearest first)" + RESET)
    # Finished tasks stay listed, in green, below the ones still to do.
    finished = [pos for pos in game.assigned_tasks if pos in game.completed_tasks]
    for pos in finished[:shown]:
        panel.append(GREEN + " ✓ " + TASK_NAMES[pos] + RESET)
    if len(finished) > shown:
        panel.append(GREEN + f"   +{len(finished) - shown} more done" + RESET)
    return panel + [""]


def nearby_panel(game: Game, width: int, visible: set[Pos]) -> list[str]:
    """Players and bodies currently in sight, nearest first; roles stay hidden."""
    if not game.player_alive:
        return []
    people = sorted((actor for actor in game.players[1:]
                     if actor.alive and actor.vent_id is None and actor.pos in visible),
                    key=lambda actor: manhattan(game.player_pos, actor.pos))
    bodies = [body for body in game.bodies if body.pos in visible]
    if not people and not bodies:
        return []
    lines = [panel_heading("IN SIGHT", width)]
    for body in bodies[:2]:
        near = manhattan(game.player_pos, body.pos) <= 1
        lines.append(align_status(f" {BOLD}{RED}†{RESET} {body.victim_name}'s body",
                                  (RED + BOLD + "R report" if near else GRAY + "body") + RESET + " ", width))
    for actor in people[:4]:
        lines.append(align_status(f" {BOLD}{actor.color}{actor.symbol}{RESET} {actor.name}",
                                  GRAY + f"{manhattan(game.player_pos, actor.pos)} away" + RESET + " ", width))
    if len(people) > 4:
        lines.append(GRAY + f"   +{len(people) - 4} more" + RESET)
    return lines + [""]


def events_panel(game: Game, width: int) -> list[str]:
    lines = [panel_heading("RECENT EVENTS", width)]
    for message in list(game.messages)[:4]:
        for index, line in enumerate(textwrap.wrap(message, width - 3)):
            lines.append((GRAY + " › " + RESET if index == 0 else "   ") + line)
    return lines


def stack_sections(sections: list[list[str]], height: int, reserve: int = 3) -> list[str]:
    """Add sections in priority order; the first one that does not fit is cut short and
    lower-priority sections are dropped. The last section fills whatever is left."""
    panel: list[str] = []
    for section in sections[:-1]:
        room = height - reserve - len(panel)
        if len(section) > room:
            panel += section[:max(0, room)]
            break
        panel += section
    return (panel + sections[-1])[:height]


def player_panel(game: Game, layout: ScreenLayout, width: int, visible: set[Pos]) -> list[str]:
    role = (impostor_panel(game, width, visible) if game.player.role == "impostor"
            else crew_panel(game, layout, width))
    ghost = ([panel_heading("GHOST", width, GRAY), GRAY + " You died: spectating." + RESET, ""]
             if not game.player_alive else [])
    return stack_sections([test_panel(game, width), sabotage_panel(game, width), ghost, role,
                           nearby_panel(game, width, visible), events_panel(game, width)], layout.height)


def map_cell(game: Game, pos: Pos, visible: set[Pos], *, zoom: int = 1) -> str:
    """The ship layout is public; current sight reveals actors and live colors."""
    x, y = pos
    in_view = pos in visible

    if game.player_alive and game.player.vent_id is not None and pos == game.player_pos:
        return BOLD + game.player.color + "▣" + RESET
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

    emergency_cell = sabotage_marker(game, pos) or alarm_terrain(game, pos, zoom, in_view)
    if emergency_cell is not None:
        return emergency_cell
    tile = game.grid[y][x]
    if tile == " ":
        return " "
    if not in_view:
        if tile in ("D", "C"):
            # A door's state is only known up close: unseen doors always read as open.
            return GRAY + OPEN_DOOR + RESET
        if tile == "O":
            return container_cell(pos, GRAY, zoom)
        if tile == "T":
            glyph = "◆" if pos in own_tasks(game) and pos not in game.completed_tasks else "◇"
        elif tile == "E":
            glyph = "◉"
        elif tile == "V":
            glyph = "▣"
        else:
            # Floor dots only mark the field of view; unseen floor stays blank.
            glyph = "#" if tile == "#" else ROOM_LABELS.get(pos, " ")
        return GRAY + glyph + RESET
    if tile == "#":
        return BLUE + "#" + RESET
    if tile == "O":
        return container_cell(pos, BLUE, zoom)
    if tile in ("D", "C"):
        return door_cell(game, pos, BLUE)
    if tile == "T":
        if pos in own_tasks(game) and pos not in game.completed_tasks:
            return BOLD + YELLOW + "◆" + RESET
        if pos in game.completed_tasks and own_tasks(game):
            return GREEN + "◇" + RESET
        return BLUE + "◇" + RESET
    if tile == "E":
        return BOLD + RED + "◉" + RESET
    if tile == "V":
        return MAGENTA + "▣" + RESET
    if pos in ROOM_LABELS:
        return BLUE + ROOM_LABELS[pos] + RESET
    return BLUE + "·" + RESET


def render_game(game: Game, size: Optional[tuple[int, int]] = None) -> str:
    size = size or terminal_size()
    layout = screen_layout(game, size)
    width = layout.panel_width or 32
    visible = game.visible_positions()
    panel = player_panel(game, layout, width, visible)

    if game.sabotage.kind and game.player_id in game.sabotage.workers:
        hint = YELLOW + (" Hold still: BOTH scanners need a player." if game.sabotage.kind == "reactor"
                         else " Repairing: stay still until complete.") + RESET
    elif game.sabotage.nearby_panel(game, game.player_id) is not None:
        hint = YELLOW + BOLD + " Press E to repair sabotage. Stay still." + RESET
    elif game.sabotage.kind:
        hint = RED + (" Emergency: reach the ! panels and press E." if game.sabotage.critical
                      else " Lights out: fix the ! panel in Electrical.") + RESET
    elif game.player.role == "impostor" and game.config.sabotage_enabled:
        hint = RED + " Sabotage ready: F Reactor / G O2 / J Admin / L Lights" + RESET
        if game.sabotage.cooldown > 1e-9:
            hint = GRAY + f" Sabotage in {math.ceil(game.sabotage.cooldown - 1e-9)}s" + RESET
    elif game.player.vent_id is not None:
        hint = MAGENTA + " Hidden | kill cooldown paused" + RESET
    elif game.active_task:
        hint = YELLOW + " Stay nearby until syncing finishes." + RESET
    elif any(manhattan(game.player_pos, p) <= 1 for p in own_tasks(game) if p not in game.completed_tasks):
        hint = YELLOW + " Press E to start the nearby task." + RESET
    elif any(manhattan(game.player_pos, b.pos) <= 1 for b in game.bodies):
        hint = RED + BOLD + " BODY FOUND — press R to report." + RESET
    elif manhattan(game.player_pos, EMERGENCY_POS) <= 1 and game.emergency_available:
        hint = RED + " E: call an emergency meeting." + RESET
    else:
        hint = GRAY + " Gray: layout" + BLUE + "  Blue: in view  " + RESET + EMERGENCY_LEGEND
    footer = [
        vent_hint(game) or " " + keys_line(MOVE_CONTROLS + [("TAB", "map"), *god_view_key(game),
                                                            ("Z", "zoom"), ("P", "panel")]),
        align_status(hint, keys_line(GLOBAL_CONTROLS) + " ", layout.width),
    ]
    return render_map_layout(game, panel, footer, visible, size)


def god_view_key(game: Game) -> list[tuple[str, str]]:
    return [("CAPS", "god view")] if game.god_view_allowed else []


MOVE_CONTROLS = [("WASD", "move"), ("E", "use"), ("R", "report"), ("V", "vent")]
GLOBAL_CONTROLS = [("T", "history"), ("H", "help"), ("Q", "quit")]


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


def ship_cell(game: Game, pos: Pos, zoom: int, structure: str, floor: str = "·",
              task: str = MAGENTA, door_states: bool = True) -> str:
    """Static ship layout (no characters): `structure` colors walls/doors, `task` your pending tasks."""
    x, y = pos
    emergency_cell = sabotage_marker(game, pos) or alarm_terrain(game, pos, zoom)
    if emergency_cell is not None:
        return emergency_cell
    tile = game.grid[y][x]
    if tile == " ":
        return " "
    if tile == "#":
        return structure + "#" + RESET
    if tile == "O":
        return container_cell(pos, structure, zoom)
    if tile in ("D", "C"):
        return door_cell(game, pos, structure) if door_states else structure + OPEN_DOOR + RESET
    if tile == "T":
        if pos in game.completed_tasks and own_tasks(game):
            return GREEN + "◇" + RESET
        if pos in own_tasks(game):
            return BOLD + task + "◆" + RESET
        return GRAY + "◇" + RESET
    if tile == "E":
        return BOLD + RED + "◉" + RESET
    if tile == "V":
        return MAGENTA + "▣" + RESET
    if pos in ROOM_LABELS:
        return WHITE + ROOM_LABELS[pos] + RESET
    return DIM + GRAY + floor + RESET


def observer_map_cell(game: Game, pos: Pos, *, zoom: int = 1) -> str:
    """God view: every character and body, on a yellow ship."""
    if game.player_alive and game.player_pos == pos:
        symbol = game.player.symbol if game.config.play_mode == "simulation" else "@"
        return BOLD + game.player.color + symbol + RESET
    for npc in game.players[1:]:
        if npc.alive and npc.pos == pos:
            return BOLD + npc.color + npc.symbol + RESET
    for body in game.bodies:
        if body.pos == pos:
            return BOLD + RED + "†" + RESET
    return ship_cell(game, pos, zoom, YELLOW)


def minimap_cell(game: Game, pos: Pos, *, zoom: int = 1) -> str:
    """Mini map: the whole ship and your own position, but no other players or bodies."""
    if game.player_alive and game.player_pos == pos and game.config.play_mode != "simulation":
        glyph = "▣" if game.player.vent_id is not None else "@"
        return BOLD + game.player.color + glyph + RESET
    # Only whoever controls the doors (impostor / spectator) sees which are closed on the map.
    return ship_cell(game, pos, zoom, BLUE, floor=" ", task=YELLOW, door_states=door_actor(game) is not None)


def npc_activity(npc: Player) -> str:
    if not npc.alive:
        return "DEAD"
    if npc.vent_id is not None:
        return "IN VENT"
    if npc.role == "impostor":
        return "HUNTING" if npc.goal is not None else "BLENDING IN"
    return "EN ROUTE" if npc.path else "SCANNING"


def manifest_entry(game: Game, actor: Player, width: int) -> str:
    """One line per player: who, role, and where they are right now."""
    symbol = "@" if actor.id == game.player_id and game.config.play_mode != "simulation" else actor.symbol
    role = RED + "IMPOSTOR" + RESET if actor.role == "impostor" else CYAN + "CREW    " + RESET
    if not actor.alive:
        return align_status(f" {GRAY}{symbol} {actor.name:<7}{RESET}{role}", GRAY + "dead " + RESET, width)
    if actor.vent_id is not None:
        place = MAGENTA + "in vent" + RESET
    elif actor.id in game.sabotage.workers:
        place = YELLOW + "repairing" + RESET
    else:
        place = CYAN + room_at(actor.pos) + RESET
    return align_status(f" {BOLD}{actor.color}{symbol}{RESET} {actor.color}{actor.name:<7}{RESET}{role}",
                        place + " ", width)


def ship_status(game: Game, width: int) -> list[str]:
    """Only what the map and HUD do not already show."""
    lines = [panel_heading("SHIP STATUS", width),
             align_status(" Kill cooldown", cooldown_text(game.kill_cooldown) + " ", width)]
    if game.bodies:
        lines.append(align_status(" Bodies unreported", RED + str(len(game.bodies)) + RESET + " ", width))
    if game.config.play_mode == "simulation" or game.player.role == "impostor":
        lines += sabotage_controls(game, width)
    return lines


def render_observer(game: Game, size: Optional[tuple[int, int]] = None) -> str:
    """God view: reveal the whole ship, players, bodies and crew state."""
    size = size or terminal_size()
    layout = screen_layout(game, size, observer=True)
    width = layout.panel_width or 32
    top = test_panel(game, width) + sabotage_panel(game, width)
    status = ship_status(game, width)
    note = [GRAY + " NO CONTROLLERS: agents inactive" + RESET] if game.config.play_mode == "simulation" else []
    # Living players first, then the dead; one line each.
    roster = sorted(game.players, key=lambda actor: not actor.alive)
    page_size = max(1, layout.height - len(top) - len(status) - len(note) - 2)
    pages = (len(roster) + page_size - 1) // page_size
    page = game.observer_page % pages
    alive = sum(actor.alive for actor in game.players)
    title = f"CREW MANIFEST {alive}/{len(game.players)}" + (f"  p{page + 1}/{pages}" if pages > 1 else "")
    panel = top + [panel_heading(title, width)] + note
    panel += [manifest_entry(game, actor, width) for actor in roster[page * page_size:(page + 1) * page_size]]
    panel += [""] + status
    controls = ([("WASD", "camera"), ("F/G/J/L", "sabotage"), ("TAB", "map/doors"), ("[ ]", "pages"),
                 ("Z", "zoom"), ("P", "panel")]
                if game.config.play_mode == "simulation"
                else MOVE_CONTROLS + [("CAPS", "player view"), ("TAB", "map"), ("Z", "zoom"), ("P", "panel")])
    last = game.chat.messages[-1] if game.chat.messages else None
    footer = [
        (" " + keys_line(controls)) if game.config.play_mode == "simulation" else vent_hint(game) or " " + keys_line(controls),
        align_status(" " + (f"{last.color}: {last.text}" if last else GRAY + "-- open door  ++ closed  " + RESET + EMERGENCY_LEGEND),
                     keys_line(GLOBAL_CONTROLS) + " ", layout.width),
    ]
    return render_map_layout(game, panel, footer, size=size, view_mode="god")


MINIMAP_LEGEND = ["@ you", "◆ your task  ◇ task", "! sabotage  ◉ emergency button",
                  "▣ vent", "-- open door  ++ closed door"]


def door_actor(game: Game) -> Optional[Player]:
    """Who closes doors: the local impostor, or a living impostor for the simulation spectator."""
    if game.config.play_mode == "simulation":
        return next((actor for actor in game.players if actor.alive and actor.role == "impostor"), None)
    return game.player if game.player.role == "impostor" and game.player_alive else None


def doors_panel(game: Game, width: int) -> list[str]:
    """Door controls on the mini map: number keys lock a room's doors."""
    doors = game.doors
    limit = game.config.max_closed_rooms
    actor = door_actor(game)
    lines = [panel_heading(f"DOORS {doors.closed_count}/{limit} ROOMS CLOSED", width, RED)]
    if actor is None:
        lines.append(GRAY + " No living impostor." + RESET)
    elif game.config.play_mode == "simulation":
        lines.append(GRAY + f" Acting as {actor.name} (impostor)." + RESET)
    if game.sabotage.kind:
        lines.append(GRAY + " Locked during sabotage." + RESET)
    elif doors.initial_cooldown > 1e-9:
        lines.append(YELLOW + f" Doors ready in {math.ceil(doors.initial_cooldown - 1e-9)}s." + RESET)
    for key, room_id in DOOR_KEYS.items():
        if room_id in doors.closed:
            state = RED + BOLD + f"++ {math.ceil(doors.closed[room_id] - 1e-9)}s" + RESET
        elif doors.cooldowns.get(room_id, 0.0) > 1e-9:
            state = GRAY + f"wait {math.ceil(doors.cooldowns[room_id] - 1e-9)}s" + RESET
        elif actor is None or doors.refusal(game, actor.id, room_id):
            state = GRAY + "--" + RESET
        else:
            state = GREEN + "-- ready" + RESET
        name = f" {BOLD}{WHITE}{key}{RESET} {DOOR_ROOMS[room_id]} {GRAY}({len(room_doors(room_id))}){RESET}"
        lines.append(align_status(name, state + " ", width))
    return lines + [GRAY + f" Press 1-{len(DOOR_KEYS)} to close a room." + RESET, ""]


def render_minimap(game: Game, size: Optional[tuple[int, int]] = None) -> str:
    """Mini map (Tab): full ship, your tasks and sabotages, without other players."""
    size = size or terminal_size()
    layout = screen_layout(game, size, observer=True)
    width = layout.panel_width or 32
    simulation = game.config.play_mode == "simulation"
    if simulation:
        role = doors_panel(game, width)
    elif door_actor(game) is not None:
        role = doors_panel(game, width) + impostor_panel(game, width, game.visible_positions())
    else:
        role = crew_panel(game, layout, width)
    legend = [panel_heading("LEGEND", width)] + [GRAY + " " + line + RESET for line in MINIMAP_LEGEND]
    panel = stack_sections([test_panel(game, width), sabotage_panel(game, width), role, legend], layout.height, 0)
    controls = ([("WASD", "camera"), ("1-7", "doors"), ("TAB", "close map"), ("Z", "zoom"), ("P", "panel")]
                if simulation else
                MOVE_CONTROLS + [("TAB", "close map"), *god_view_key(game), ("Z", "zoom"), ("P", "panel")])
    footer = [
        (" " + keys_line(controls)) if simulation else vent_hint(game) or " " + keys_line(controls),
        align_status(" " + GRAY + "Players are hidden on the map." + RESET,
                     keys_line(GLOBAL_CONTROLS) + " ", layout.width),
    ]
    return render_map_layout(game, panel, footer, size=size, view_mode="minimap")


def render_help(game: Game) -> str:
    content = [
        "HOW TO PLAY // THE SKELD",
        "Map known: gray layout, blue in sight.",
        "",
        "WASD / arrows move",
        "E             use / interact",
        "◉             emergency button: E to call",
        "R             report a body; K to kill",
        "V             vent in/out; 1/2 travel inside",
        "F / G / J     sabotage Reactor / O2 / Admin",
        "L             sabotage Lights (crew vision)",
        "E at !        repair; stay still until done",
        "Reactor needs TWO players holding panels.",
        "Tab           mini map (no players)",
        "1-7 in map    impostor: close a room's doors",
        *(["Caps Lock     god view while it is on"] if game.god_view_allowed else []),
        "Z             toggle map zoom (1X / 2X)",
        "P             show / hide side panel",
        "[ / ]         previous / next crew page",
        "T             chat (write during voting)",
        "H             close help",
        "Q             quit game",
        *(["X / N         test: swap role / reset"] if game.test_mode else []),
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
                   "F / G / J     sabotage Reactor / O2 / Admin",
                   "L             sabotage Lights (crew vision)",
                   "Tab, then 1-7 close a room's doors",
                   f"Sabotage: {game.config.sabotage_seconds:g}s, cooldown {game.config.sabotage_cooldown:g}s.",
                   "Crew has no automatic repair behavior.",
                   "T             broadcast chat (read only)",
                   "H             close help", "Q             stop simulation", "",
                   "No movement, tasks, chat or votes by agents.",
                   "Game NPCs run only in Game mode.",
                   f"Full map + panel: at least {MAP_W + 36}x{MAP_H + 8}.",
                   "R on the final screen returns to setup."]
    if game.sabotage.kind:
        content[0] = sabotage_alert(game)
    box = [CYAN + "╔" + "═" * 44 + "╗" + RESET]
    box += ["║ " + fit_line(line, 42, pad=True) + " ║" for line in content]
    box.append(CYAN + "╚" + "═" * 44 + "╝" + RESET)
    return fit_screen(box)


VOTE_KEYS = "123456789abc"


def meeting_reason(game: Game, reporter_id: str, body: Optional[Body]) -> str:
    reporter = game.entity(reporter_id)
    who = BOLD + reporter.color + reporter.name + RESET
    if body is None:
        return BOLD + YELLOW + "Emergency button" + RESET + GRAY + " pressed by " + RESET + who + GRAY + "." + RESET
    victim = game.entity(body.victim_id)
    # The room stays secret: the reporter has to say where in the chat.
    return (BOLD + victim.color + body.victim_name + RESET + " body found." + GRAY + " Reported by " + RESET
            + who + GRAY + "." + RESET)


def vote_choice(game: Game, key: str, actor: Player) -> str:
    """One ballot entry: key, colored name (@ marks you), and whether they voted yet."""
    you = " @" if actor.id == game.player_id and game.config.play_mode == "game" else ""
    status = GREEN + "VOTED  " + RESET if actor.id in game.meetings.votes else YELLOW + "WAITING" + RESET
    return (f" {BOLD}{WHITE}[{key.upper()}]{RESET} {actor.color}●{RESET} "
            f"{BOLD if you else ''}{actor.color}{actor.name + you:<9}{RESET}{status}")


def render_meeting(game: Game, reporter_id: str, body: Optional[Body], prompt: str = "",
                   size: Optional[tuple[int, int]] = None) -> str:
    size = size or terminal_size()
    inner = 55
    width = inner - 2
    meetings = game.meetings
    alive = [game.entity(entity_id) for entity_id in game.alive_ids()]
    voted = sum(actor.id in meetings.votes for actor in alive)
    remaining = math.ceil(meetings.remaining)
    clock_color = RED if meetings.remaining <= 10 else YELLOW
    clock = f"{clock_color}{BOLD}{remaining:>2}s{RESET}"
    tally = f"{GRAY}{voted}/{len(alive)} voted{RESET}"
    bar_width = width - 4 - display_width(tally) - 3
    timer = clock + " " + progress_bar(meetings.remaining / max(1e-9, meetings.duration), bar_width) + "  " + tally

    content = [meeting_reason(game, reporter_id, body), timer, None,
               BOLD + WHITE + "Who should be ejected?" + RESET]
    choices = [vote_choice(game, key, actor) for key, actor in zip(VOTE_KEYS, alive)]
    for index in range(0, len(choices), 2):
        content.append(fit_line(choices[index], 27, pad=True)
                       + (choices[index + 1] if index + 1 < len(choices) else ""))
    dead = [actor for actor in game.players if not actor.alive]
    if dead:
        content.append(GRAY + " ✕ Dead: " + ", ".join(actor.name for actor in dead) + RESET)
    content.append(None)
    content.append(f" {BOLD}{WHITE}[0]{RESET} Skip vote  {GRAY}· no vote at timeout = skip{RESET}")
    if game.player_id in meetings.votes:
        choice = meetings.votes[game.player_id]
        target = (game.entity(choice).color + game.name_of(choice) + RESET) if choice else GRAY + "Skip" + RESET
        content.append(" " + GREEN + BOLD + "✓ Your vote: " + RESET + target
                       + GRAY + "  · waiting for the others" + RESET)
    elif prompt:
        content.append(" " + prompt)
    else:
        content.append(" " + GRAY + "Pick a key to vote. Talk first: T or Tab chat." + RESET)
    content.append(GRAY + " 1-9/A-C vote · 0 skip · T/Tab chat · Q/Esc quit" + RESET)

    title = " EMERGENCY MEETING "
    lines = [RED + "╭─" + BOLD + title + RESET + RED + "─" * (inner - len(title) - 1) + "╮" + RESET]
    for line in content:
        if line is None:
            lines.append(RED + "├" + "─" * inner + "┤" + RESET)
        else:
            lines.append(RED + "│ " + RESET + fit_line(line, width, pad=True) + RED + " │" + RESET)
    lines.append(RED + "╰" + "─" * inner + "╯" + RESET)
    return centered_screen(lines, size)


def render_vote_result(
    game: Game,
    ejected: Optional[str],
    counts: dict[Optional[str], int],
    seconds_left: Optional[float] = None,
) -> str:
    """Ballot tally as bars, then who left the ship (or why nobody did)."""
    inner = 55
    width = inner - 2
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0] is None, game.name_of(item[0]) if item[0] else ""))
    top = max(counts.values(), default=0)
    leaders = [target for target, count in ranked if count == top]
    rows = []
    for target, count in ranked:
        if target is None:
            label = GRAY + "○ Skip" + RESET
        else:
            actor = game.entity(target)
            label = BOLD + actor.color + "● " + actor.name + RESET
        if target == ejected and ejected is not None:
            tag = RED + BOLD + " ◀ ejected" + RESET
        elif ejected is None and count == top and len(leaders) > 1:
            tag = YELLOW + " ◀ tie" + RESET
        else:
            tag = ""
        # One dot per vote.
        bar = (GRAY if target is None else YELLOW) + " ".join("●" * count) + RESET
        rows.append(fit_line(label, 11, pad=True) + bar + f"  {BOLD}{count}{RESET}" + tag)

    impostors = sum(actor.alive and actor.role == "impostor" for actor in game.players)
    remaining = (f"{impostors} impostor{'s' if impostors != 1 else ''} remain{'s' if impostors == 1 else ''}."
                 if impostors else "No impostors remain.")
    if ejected is None:
        names = [("Skip" if target is None else game.name_of(target)) for target in leaders]
        why = (f"Tie: {', '.join(names)} ({top} each)." if len(leaders) > 1
               else "Most players skipped." if leaders == [None] else "Not enough votes.")
        outcome = [BOLD + WHITE + "Nobody was ejected." + RESET, GRAY + why + RESET]
    else:
        actor = game.entity(ejected)
        who = "You were" if ejected == game.player_id else "They were"
        verdict = "the impostor." if actor.role == "impostor" else "not the impostor."
        outcome = [BOLD + actor.color + actor.name + RESET + BOLD + " was ejected." + RESET,
                   (RED if actor.role == "impostor" else GRAY) + f"{who} {verdict}" + RESET
                   + GRAY + f"  {remaining}" + RESET]
    left = f" in {math.ceil(seconds_left)}s" if seconds_left is not None else ""
    if game.config.play_mode == "simulation":
        hint = f"Simulation resumes automatically{left}."
    else:
        hint = "Press any key to continue" + (f" · back to the ship{left}" if left else ".")

    title = " VOTING RESULTS "
    lines = [CYAN + "╭─" + BOLD + title + RESET + CYAN + "─" * (inner - len(title) - 1) + "╮" + RESET]
    lines += [CYAN + "│ " + RESET + fit_line(row, width, pad=True) + CYAN + " │" + RESET for row in rows]
    lines.append(CYAN + "├" + "─" * inner + "┤" + RESET)
    lines += [CYAN + "│ " + RESET + fit_line(line, width, pad=True) + CYAN + " │" + RESET
              for line in outcome + [DIM + hint + RESET]]
    lines.append(CYAN + "╰" + "─" * inner + "╯" + RESET)
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
