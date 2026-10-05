"""Responsive broadcast chat, anchored history, and a persistent text composer."""
from __future__ import annotations
import math
from dataclasses import dataclass
from ..core.engine import Game
from .palette import BOLD, CYAN, DARK, GRAY, GREEN, RED, RESET, WHITE, YELLOW
from .text import (MIN_COLUMNS, MIN_ROWS, cell_width, centered_screen, display_width,
                   fit_line, fit_screen, terminal_size)


@dataclass(frozen=True)
class ChatRow:
    sequence: int
    part: int
    text: str


def wrap_ranges(text: str, width: int) -> list[tuple[int, int]]:
    """Wrap at spaces where possible, retaining every character and its index."""
    width = max(2, width)
    result = []
    start = 0
    while start < len(text):
        end, used, space = start, 0, None
        while end < len(text) and used + cell_width(text[end]) <= width:
            used += cell_width(text[end])
            if text[end] == " ":
                space = end
            end += 1
        if end < len(text) and space is not None and space > start:
            end = space + 1
        result.append((start, end))
        start = end
    return result or [(0, 0)]


def timestamp(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def right_align(text: str, width: int) -> str:
    return " " * max(0, width - display_width(text)) + text


def split_line(left: str, right: str, width: int) -> str:
    return fit_line(left, max(0, width - display_width(right) - 1), pad=True) + " " + right


class ChatView:
    def __init__(self) -> None:
        self._draft = ""
        self.cursor = 0
        self.feedback = ""
        self.feedback_color = GRAY
        self.scroll = 0
        self._rows: list[ChatRow] = []
        self._page_lines = 1
        self._anchor: tuple[int, int] | None = None
        self._seen_sequence: int | None = None

    @property
    def draft(self) -> str:
        return self._draft

    @draft.setter
    def draft(self, value: str) -> None:
        self._draft = value
        self.cursor = len(value)

    @staticmethod
    def can_send(game: Game) -> bool:
        return game.config.play_mode == "game" and game.chat.can_send(game, game.player_id)

    def latest(self) -> None:
        self.scroll, self._anchor = 0, None

    def scroll_by(self, amount: int) -> None:
        maximum = max(0, len(self._rows) - self._page_lines) if self._rows else max(0, amount)
        self.scroll = min(maximum, max(0, self.scroll + amount))
        self._anchor = None
        if self.scroll and self._rows:
            row = self._rows[len(self._rows) - self.scroll - 1]
            self._anchor = (row.sequence, row.part)

    def handle_key(self, game: Game, key: str) -> bool:
        """Return True to close; text never invokes movement, votes, or Quit."""
        if key == "escape":
            return True
        if key == "\t":
            self.latest()
        elif key in ("up", "down", "pageup", "pagedown"):
            amount = self._page_lines if key in ("pageup", "pagedown") else 1
            self.scroll_by(amount if key in ("up", "pageup") else -amount)
        elif self.can_send(game):
            self.cursor = min(self.cursor, len(self.draft))
            if key in ("\r", "\n"):
                if game.chat.send(game, game.player_id, self.draft):
                    self.draft, self.feedback = "", "Sent to everyone."
                    self.feedback_color = GREEN
                    self.latest()
                else:
                    self.feedback, self.feedback_color = "Write a message first.", YELLOW
            elif key in ("left", "right", "home", "end"):
                if key == "home":
                    self.cursor = 0
                elif key == "end":
                    self.cursor = len(self.draft)
                else:
                    self.cursor = max(0, min(len(self.draft), self.cursor + (1 if key == "right" else -1)))
            elif key in ("\x08", "\x7f"):
                if self.cursor:
                    self._draft = self.draft[:self.cursor - 1] + self.draft[self.cursor:]
                    self.cursor -= 1
                self.feedback = ""
            elif key == "delete":
                self._draft = self.draft[:self.cursor] + self.draft[self.cursor + 1:]
                self.feedback = ""
            elif len(key) == 1 and key.isprintable():
                if len(self.draft) < game.config.chat_max_length:
                    self._draft = self.draft[:self.cursor] + key + self.draft[self.cursor:]
                    self.cursor += 1
                    self.feedback = ""
                else:
                    self.feedback, self.feedback_color = "Message limit reached.", YELLOW
        return False

    def history(self, game: Game, width: int, height: int, compact: bool) -> tuple[list[str], str]:
        rows = message_rows(game, width, compact)
        if self._seen_sequence is None:
            self._seen_sequence = game.chat.sequence
        if self.scroll and self._anchor and rows:
            sequence, part = self._anchor
            matching = [index for index, row in enumerate(rows) if row.sequence == sequence]
            # Evicted messages fall back to the oldest retained page.
            end = matching[min(part, len(matching) - 1)] + 1 if matching else height
            self.scroll = len(rows) - end
        self.scroll = min(max(0, self.scroll), max(0, len(rows) - height))
        end = len(rows) - self.scroll
        start = max(0, end - height)
        self._rows, self._page_lines = rows, height
        if self.scroll:
            row = rows[end - 1]
            self._anchor = (row.sequence, row.part)
            unread = game.chat.sequence - self._seen_sequence
            label = YELLOW + (f"{unread} new | Tab latest" if unread else "Reading history") + RESET
        else:
            self._anchor, self._seen_sequence = None, game.chat.sequence
            label = ""  # At the newest message there is nothing to report.
        visible = [row.text for row in rows[start:end]]
        if not rows:
            hint = ("Be the first to speak: share what you saw." if self.can_send(game)
                    else "Messages can be sent during voting.")
            empty = [BOLD + WHITE + "No messages yet." + RESET, GRAY + hint + RESET]
            top = max(0, (height - len(empty)) // 2)
            visible = ([""] * top + [" " * max(0, (width - display_width(line)) // 2) + line for line in empty])
            visible += [""] * (height - len(visible))
        else:
            visible = [""] * (height - len(visible)) + visible
        if len(rows) <= height:  # Everything fits: no scrollbar.
            return [fit_line(line, width, pad=True) + "  " for line in visible], label
        thumb_size = max(1, height * height // max(height, len(rows)))
        thumb_top = (height - thumb_size) * start // max(1, len(rows) - height)
        return [fit_line(line, width, pad=True) + " "
                + (CYAN + "┃" if thumb_top <= index < thumb_top + thumb_size else DARK + "│") + RESET
                for index, line in enumerate(visible)], label


GROUP_SECONDS = 30  # Consecutive messages from one player within this window share a header.


def message_rows(game: Game, width: int, compact: bool = False) -> list[ChatRow]:
    rows = []
    previous = None
    for message in game.chat.messages:
        color = game.entity(message.sender).color
        own = (message.sender == game.player_id and game.config.play_mode == "game")
        author = BOLD + color + message.color + RESET + (GRAY + " (you)" + RESET if own else "")
        clock = GRAY + timestamp(message.time) + RESET
        if compact:
            if own:  # Your messages hug the right edge: `you: text 00:00`.
                prefix = author + ": "
                indent = display_width(prefix)
                parts = wrap_ranges(message.text, width - indent - 6)
                lines = [right_align((prefix if index == 0 else "") + WHITE + message.text[start:end].rstrip() + RESET
                                     + (" " + clock if index == 0 else "      "), width)
                         for index, (start, end) in enumerate(parts)]
            else:
                prefix = clock + " " + author + ": "
                indent = display_width(prefix)
                parts = wrap_ranges(message.text, width - indent)
                lines = [(prefix if index == 0 else " " * indent) + WHITE + message.text[start:end] + RESET
                         for index, (start, end) in enumerate(parts)]
        else:
            grouped = (previous is not None and previous.sender == message.sender
                       and message.time - previous.time <= GROUP_SECONDS)
            if grouped and rows and rows[-1].text == "":
                rows.pop()  # Join the previous message's block.
            # Bubbles take about three quarters of the width, so the two sides stay apart.
            bubble = max(20, width * 3 // 4)
            chunks = [message.text[start:end].rstrip() for start, end in wrap_ranges(message.text, bubble - 2)]
            if own:  # Right side, mirrored: time, name, dot; text bar on the right.
                lines = [] if grouped else [right_align(clock + "  " + author + " " + color + "●" + RESET, width)]
                lines += [right_align(WHITE + chunk + RESET + " " + color + "│" + RESET, width) for chunk in chunks]
            else:
                lines = [] if grouped else [split_line(color + "● " + RESET + author, clock, bubble)]
                lines += [color + "│ " + RESET + WHITE + chunk + RESET for chunk in chunks]
            lines.append("")
        previous = message
        rows.extend(ChatRow(message.sequence, index, line) for index, line in enumerate(lines))
    if rows and rows[-1].text == "":
        rows.pop()  # No gap under the newest message.
    return rows


SIDEBAR_WIDTH = 22


def crew_sidebar(game: Game, height: int) -> list[str]:
    """Who is in the conversation: living players (with ballots while voting), then the dead."""
    voting = bool(game.pending_meeting)
    voted = sum(actor.id in game.meetings.votes for actor in game.players if actor.alive)
    alive = sum(actor.alive for actor in game.players)
    title = f"CREW {voted}/{alive} voted" if voting else f"CREW {alive} alive"
    lines = [BOLD + CYAN + title + RESET, DARK + "─" * (SIDEBAR_WIDTH - 1) + RESET]
    for actor in sorted(game.players, key=lambda actor: not actor.alive):
        you = GRAY + " (you)" + RESET if actor.id == game.player_id and game.config.play_mode == "game" else ""
        if not actor.alive:
            lines.append(GRAY + "✕ " + actor.name + RESET + you)
            continue
        mark = ""
        if voting:
            mark = GREEN + "✓" + RESET if actor.id in game.meetings.votes else GRAY + "…" + RESET
        name = BOLD + actor.color + "● " + RESET + actor.color + actor.name + RESET + you
        lines.append(split_line(name, mark, SIDEBAR_WIDTH - 1) if mark else name)
    if len(lines) > height:
        lines = lines[:height - 1] + [GRAY + f"+{len(lines) - height + 1} more" + RESET]
    return [fit_line(line, SIDEBAR_WIDTH - 1, pad=True) for line in lines + [""] * (height - len(lines))]


def meeting_context(game: Game) -> str:
    if not game.pending_meeting:
        return GRAY + "Live game" + RESET
    reporter_id, body = game.pending_meeting
    reporter = game.entity(reporter_id)
    who = reporter.color + reporter.name + RESET
    if body is not None:
        victim = game.entity(body.victim_id)
        return BOLD + RED + "Body found" + RESET + GRAY + ": " + RESET + victim.color + body.victim_name + RESET + GRAY + " · reported by " + RESET + who
    return BOLD + YELLOW + "Emergency meeting" + RESET + GRAY + " · called by " + RESET + who


def composer_lines(view: ChatView, width: int, height: int) -> list[str]:
    if not view.draft:
        return [CYAN + "> " + "\x1b[7m " + RESET + GRAY + "Type a message to everyone..." + RESET] + [""] * (height - 1)
    text = view.draft + " "  # Reserve a visible caret when editing at the end.
    spans = wrap_ranges(text, width - 2)
    cursor = min(view.cursor, len(view.draft))
    cursor_line = next(index for index, (start, end) in enumerate(spans) if start <= cursor < end)
    first = max(0, cursor_line - height + 1)
    lines = []
    for index, (start, end) in enumerate(spans[first:first + height], start=first):
        chunk = WHITE + text[start:end] + RESET
        if start <= cursor < end:
            chunk = (WHITE + text[start:cursor] + "\x1b[7m" + text[cursor:cursor + 1]
                     + RESET + WHITE + text[cursor + 1:end] + RESET)
        marker = "↑ " if index == first and first else "↓ " if index == first + height - 1 and end < len(text) else "> " if index == 0 else "  "
        lines.append(CYAN + marker + chunk)
    return lines + [""] * (height - len(lines))


def render_chat(game: Game, view: ChatView, size: tuple[int, int] | None = None) -> str:
    size = size or terminal_size()
    if size[0] < MIN_COLUMNS or size[1] < MIN_ROWS:
        return fit_screen([], size)
    inner = min(110, size[0] - 5)
    width = inner - 2
    height = min(42, size[1] - 1)
    compact = size[0] < 80 or size[1] < 26
    draft_height = 2 if compact else 3
    header_rows = 1 if compact else 2
    history_height = height - 7 - header_rows - draft_height
    sidebar = not compact and width >= 80
    history_width = width - 2 - (SIDEBAR_WIDTH + 2 if sidebar else 0)
    history, position = view.history(game, history_width, history_height, compact)
    if sidebar:
        crew = crew_sidebar(game, history_height)
        history = [line + DARK + " │ " + RESET + side for line, side in zip(history, crew)]
    if game.pending_meeting:
        color = RED if game.meetings.remaining <= 10 else YELLOW
        status = color + BOLD + "Voting " + timestamp(math.ceil(game.meetings.remaining)) + RESET
    else:
        status = GREEN + "World running" + RESET

    def row(text: str) -> str:
        return CYAN + "│ " + RESET + fit_line(text, width, pad=True) + CYAN + " │" + RESET

    separator = CYAN + "├" + "─" * inner + "┤" + RESET
    back = "Esc voting" if game.pending_meeting else "Esc back"
    alive = sum(actor.alive for actor in game.players)
    audience = GRAY + f"To everyone · {alive} alive" + RESET
    lines = [CYAN + "╭─" + BOLD + " BROADCAST CHAT " + RESET + CYAN + "─" * max(0, inner - 17) + "╮" + RESET]
    if compact:
        lines.append(row(split_line(audience, status, width)))
    else:
        lines += [row(split_line(meeting_context(game), status, width)), row(audience)]
    lines.append(separator)
    lines += [row(line) for line in history]
    scrollable = len(view._rows) > history_height
    hint = GRAY + "↑/↓ scroll  PgUp/PgDn page" + RESET if scrollable else ""
    lines += [row(split_line(position, hint, width)), separator]
    if view.can_send(game):
        near = len(view.draft) >= 0.9 * game.config.chat_max_length
        count_color = RED if len(view.draft) >= game.config.chat_max_length else YELLOW if near else GRAY
        count = count_color + f"{len(view.draft)}/{game.config.chat_max_length}" + RESET
        author = BOLD + game.player.color + game.player.name + RESET + " → everyone"
        label = view.feedback_color + view.feedback + RESET if view.feedback else author
        lines.append(row(split_line(label, count, width)))
        lines += [row(line) for line in composer_lines(view, width, draft_height)]
        lines.append(row(GRAY + f"Enter send  ·  {back}  ·  Tab latest  ·  ←/→ edit" + RESET))
    else:
        reason = ("You were eliminated. You can still read messages." if not game.player_alive
                  else "Spectator mode. Follow the conversation here." if game.config.play_mode == "simulation"
                  else "The game has ended. You can read the history." if game.outcome
                  else "Messages can be sent only during voting.")
        lines.append(row(YELLOW + BOLD + "Read only" + RESET))
        lines += [row(reason)] + [row("")] * (draft_height - 1)
        lines.append(row(GRAY + f"{back}  ·  Tab latest" + RESET))
    lines.append(CYAN + "╰" + "─" * inner + "╯" + RESET)
    return centered_screen(lines, size)
