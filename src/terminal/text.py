"""Terminal sizing and ANSI-aware text layout."""
from __future__ import annotations
import re
import shutil
import unicodedata
from typing import Optional
from .palette import RESET

ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")
MIN_COLUMNS = 60
MIN_ROWS = 20


def terminal_size() -> tuple[int, int]:
    columns, rows = shutil.get_terminal_size((120, 38))
    return columns, rows


def cell_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def display_width(text: str) -> int:
    return sum(cell_width(char) for char in ANSI_SGR.sub("", text))


def fit_line(text: str, width: int, pad: bool = False) -> str:
    """Clip by terminal cells, preserving complete ANSI color sequences."""
    width = max(0, width)
    result: list[str] = []
    used = 0
    index = 0
    while index < len(text):
        match = ANSI_SGR.match(text, index)
        if match:
            result.append(match.group())
            index = match.end()
            continue
        char = text[index]
        char_width = cell_width(char)
        if used + char_width > width:
            break
        result.append(char)
        used += char_width
        index += 1
    return "".join(result) + RESET + (" " * (width - used) if pad else "")


def fit_screen(lines: list[str], size: Optional[tuple[int, int]] = None) -> str:
    columns, rows = size or terminal_size()
    if columns < MIN_COLUMNS or rows < MIN_ROWS:
        lines = ["Terminal too small: enlarge the window.",
                 f"Minimum: {MIN_COLUMNS}x{MIN_ROWS} | Current: {columns}x{rows}",
                 "Q / Esc: quit"]
    return "\n".join(fit_line(line, columns - 1) for line in lines[:max(1, rows - 1)])


def centered_screen(lines: list[str], size: Optional[tuple[int, int]] = None) -> str:
    """Center a content block inside the usable terminal area, preserving ANSI colors."""
    size = size or terminal_size()
    columns, rows = size
    if columns < MIN_COLUMNS or rows < MIN_ROWS:
        return fit_screen(lines, size)
    width, height = columns - 1, rows - 1
    content = [fit_line(line, width) for line in lines[:height]]
    block_width = max((display_width(line) for line in content), default=0)
    left = (width - block_width) // 2
    top = (height - len(content)) // 2
    return fit_screen([""] * top + [" " * left + line for line in content], size)
