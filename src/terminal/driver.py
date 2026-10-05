"""Incremental terminal output and cross-platform keyboard input."""
from __future__ import annotations
import os
import sys
from typing import Optional
from .palette import RESET
from .text import fit_line, terminal_size

# Windows console: special keys arrive as "\x00"/"\xe0" followed by one of these codes.
WINDOWS_KEYS = {"H": "up", "P": "down", "K": "left", "M": "right", "I": "pageup", "Q": "pagedown",
                "G": "home", "O": "end", "S": "delete"}
UNIX_KEYS = {b"\x1b[A": "up", b"\x1b[B": "down", b"\x1b[D": "left", b"\x1b[C": "right",
             b"\x1b[5~": "pageup", b"\x1b[6~": "pagedown", b"\x1b[H": "home", b"\x1b[F": "end",
             b"\x1b[1~": "home", b"\x1b[4~": "end", b"\x1b[3~": "delete",
             b"\x1bOA": "up", b"\x1bOB": "down", b"\x1bOD": "left", b"\x1bOC": "right",
             b"\x1bOH": "home", b"\x1bOF": "end"}

class Terminal:
    """Cross-platform raw, non-blocking keyboard input and ANSI output."""

    def __init__(self) -> None:
        self.is_windows = os.name == "nt"
        self.old_settings = None
        self.old_flags = None
        self.old_output_mode = None
        self.buffer = b""
        self._drawn_lines: tuple[str, ...] = ()
        self._drawn_size: Optional[tuple[int, int]] = None

    def __enter__(self) -> "Terminal":
        if self.is_windows:
            try:
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.windll.kernel32
                kernel.GetStdHandle.argtypes = [wintypes.DWORD]
                kernel.GetStdHandle.restype = wintypes.HANDLE
                kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                self.output_handle = kernel.GetStdHandle(-11)
                mode = wintypes.DWORD()
                if kernel.GetConsoleMode(self.output_handle, ctypes.byref(mode)):
                    self.old_output_mode = mode.value
                    kernel.SetConsoleMode(self.output_handle, mode.value | 0x0004)
                ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            except Exception:
                pass
        elif sys.stdin.isatty():
            import fcntl
            import termios
            import tty
            fd = sys.stdin.fileno()
            self.old_settings = termios.tcgetattr(fd)
            self.old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            tty.setcbreak(fd)
            fcntl.fcntl(fd, fcntl.F_SETFL, self.old_flags | os.O_NONBLOCK)
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H")
        sys.stdout.flush()
        self._drawn_lines = ()
        self._drawn_size = None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.is_windows and self.old_settings is not None:
            import fcntl
            import termios
            fd = sys.stdin.fileno()
            termios.tcsetattr(fd, termios.TCSADRAIN, self.old_settings)
            if self.old_flags is not None:
                fcntl.fcntl(fd, fcntl.F_SETFL, self.old_flags)
        sys.stdout.write(RESET + "\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        if self.is_windows and self.old_output_mode is not None:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(self.output_handle, self.old_output_mode)

    def caps_lock(self) -> Optional[bool]:
        """Caps Lock state, or None where the terminal cannot report it.
        Terminals never send Caps Lock as a key, so Windows asks the OS directly."""
        if not self.is_windows:
            return None
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)  # VK_CAPITAL toggle bit
        except (AttributeError, OSError):
            return None

    def read_keys(self) -> list[str]:
        if self.is_windows:
            return self._read_windows()
        return self._read_unix()

    def _read_windows(self) -> list[str]:
        import msvcrt
        keys: list[str] = []
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                # Special keys are a prefix plus a code. Always read the code: checking
                # kbhit() first could drop the prefix and leak the code ("H"/"P" while
                # scrolling) into the chat draft as text.
                keys.append(WINDOWS_KEYS.get(msvcrt.getwch(), ""))
            elif ch == "\x1b":
                keys.append("escape")
            elif ch == "\x03":
                raise KeyboardInterrupt
            else:
                keys.append(ch)
        return [key for key in keys if key]

    def _read_unix(self) -> list[str]:
        import select
        if not sys.stdin.isatty():
            return []
        chunks = []
        while select.select([sys.stdin], [], [], 0)[0]:
            try:
                chunks.append(os.read(sys.stdin.fileno(), 64))
            except BlockingIOError:
                break
        self.buffer += b"".join(chunks)
        return self.parse_unix_buffer()

    def parse_unix_buffer(self) -> list[str]:
        """Turn buffered bytes into keys; escape sequences never leak as text."""
        keys: list[str] = []
        while self.buffer:
            if self.buffer.startswith(b"\x1b[") or self.buffer.startswith(b"\x1bO"):
                # CSI/SS3: parameters, then one final byte in 0x40-0x7E.
                end = next((index for index in range(2, len(self.buffer))
                            if 0x40 <= self.buffer[index] <= 0x7E), None)
                if end is None:
                    break  # Wait for the rest of the sequence.
                sequence, self.buffer = self.buffer[:end + 1], self.buffer[end + 1:]
                keys.append(UNIX_KEYS.get(sequence, ""))  # Unknown sequences are dropped.
                continue
            if self.buffer.startswith(b"\x1b"):
                keys.append("escape")
                self.buffer = self.buffer[1:]
                continue
            first = self.buffer[0]
            length = 1 if first < 0x80 else 2 if 0xC2 <= first <= 0xDF else 3 if 0xE0 <= first <= 0xEF else 4 if 0xF0 <= first <= 0xF4 else 1
            if len(self.buffer) < length:
                break  # Keep a partial UTF-8 character for the next read.
            raw = self.buffer[:length]
            try:
                char = raw.decode("utf-8")
            except UnicodeDecodeError:
                self.buffer = self.buffer[1:]
                continue
            self.buffer = self.buffer[length:]
            if char == "\x03":
                raise KeyboardInterrupt
            keys.append(char)
        return [key for key in keys if key]

    def draw(self, text: str) -> None:
        columns, rows = terminal_size()
        height = max(1, rows - 1)
        source_lines = text.splitlines()[:height]
        # Pad short and missing lines so new content overwrites old content
        # directly. Erasing a row before repainting exposes a blank frame.
        lines = tuple(fit_line(source_lines[index] if index < len(source_lines) else "",
                               columns - 1, pad=True)
                      for index in range(height))
        resized = self._drawn_size != (columns, rows)
        updates = []
        for index, line in enumerate(lines):
            if resized or line != self._drawn_lines[index]:
                # Absolute positions and a reserved last column prevent wrap.
                # Clear only the unused last column, after writing the row.
                updates.append(f"\x1b[{index + 1};1H" + RESET + line + "\x1b[K")
        if resized:
            # A terminal resize may reflow old content into the reserved row.
            updates.append(f"\x1b[{rows};1H" + RESET + "\x1b[K")
        if not updates:
            return
        # Send a single batch; unchanged rows stay on screen throughout.
        sys.stdout.write("".join(updates))
        sys.stdout.flush()
        self._drawn_lines = lines
        self._drawn_size = (columns, rows)
