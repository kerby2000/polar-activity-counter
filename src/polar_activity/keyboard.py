"""Polling keyboard input; no blocking input() or uncancellable input thread."""

import os
import sys
from collections.abc import Callable

from .labels import ACTIVITIES, Labels

HELP = (
    "1 pull-up | 2 push-up | 3 squat | 4 jump | 5 walking | 6 stairs | 7 household\n"
    "8 standing | 9 sitting | 0 other | A custom activity | SPACE start/end set\n"
    "N note (current/latest set) | Q finish | Enter submits text/count; Esc cancels prompt"
)


class Keyboard:
    def __init__(self, enabled: bool):
        self.enabled = enabled and sys.stdin.isatty()
        self.saved = None
        self.extended = False

    def __enter__(self):
        if self.enabled and os.name != "nt":
            import termios
            import tty

            self.saved = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *_args):
        if self.saved is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.saved)

    def read(self) -> str | None:
        if not self.enabled:
            return None
        if os.name == "nt":
            import msvcrt

            if not msvcrt.kbhit():
                return None
            char = msvcrt.getwch()
            if self.extended:
                self.extended = False
                return None
            if char in ("\x00", "\xe0"):
                self.extended = True
                return None
            return char
        import select

        return sys.stdin.read(1) if select.select([sys.stdin], [], [], 0)[0] else None


class LabelController:
    def __init__(
        self,
        labels: Labels,
        emit: Callable[[str], None] = print,
        echo: Callable[[str], None] | None = None,
    ):
        self.labels = labels
        self.emit = emit
        self.echo = echo or (lambda value: print(value, end="", flush=True))
        self.prompt: str | None = None
        self.buffer = ""
        self.pending_set: dict | None = None

    def key(self, char: str) -> bool:
        if char == "\x03":
            return True
        if self.prompt:
            if char == "\x1b":
                self.prompt = None
                self.buffer = ""
                self.emit("\nPrompt cancelled; missing count remains unknown.")
            elif char in ("\r", "\n"):
                self.emit("")
                try:
                    if self.prompt == "reps":
                        self.labels.reps(self.pending_set, self.buffer)
                    elif self.prompt == "note":
                        self.labels.note(self.buffer)
                    else:
                        self.labels.select(self.buffer)
                    self.prompt = None
                    self.buffer = ""
                except ValueError as exc:
                    self.emit(str(exc))
                    self.buffer = ""
            elif char in ("\b", "\x7f"):
                if self.buffer:
                    self.buffer = self.buffer[:-1]
                    self.echo("\b \b")
            elif char.isprintable():
                self.buffer += char
                self.echo(char)
            return False
        char_lower = char.lower()
        try:
            if char in ACTIVITIES:
                self.labels.select(ACTIVITIES[char])
                self.emit(f"Activity: {self.labels.activity}")
            elif char == " ":
                item = self.labels.toggle()
                if self.labels.active:
                    self.emit(f"Set {item['set_id']} started: {item['activity']}")
                else:
                    self.pending_set = item
                    self.prompt = "reps"
                    self.emit(
                        f"Set {item['set_id']} ended "
                        f"({item['end_time_s'] - item['start_time_s']:.1f}s). "
                        "Repetitions? Enter for unknown/background:"
                    )
            elif char_lower == "n":
                self.prompt = "note"
                self.emit("Note:")
            elif char_lower == "a":
                if self.labels.active:
                    raise ValueError("End the current set before changing activity")
                self.prompt = "activity"
                self.emit("Activity (e.g. reaching-overhead, carrying, dressing):")
            elif char_lower == "q":
                return True
        except ValueError as exc:
            self.emit(str(exc))
        return False
