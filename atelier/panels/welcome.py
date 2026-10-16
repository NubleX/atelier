"""Welcome screen (the home tab) and the new-tab picker popup. Click or Enter opens a project as a tab."""
from __future__ import annotations

import getpass
import json
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

from atelier import state as st
from atelier.projects import Project, discover
from atelier.theme import AURELIA, clip, fmt_age, gradient, style, wordmark
from atelier.tmuxctl import list_windows, run

BIN = str(Path(__file__).resolve().parent.parent.parent / "bin" / "atelier")
CLAUDE_DIR = Path.home() / ".claude"
WORDS = ("Dreaming", "Weaving", "Composing", "Conjuring", "Tracing")  # the Aurelia skin's spinner words


@dataclass(frozen=True)
class Entry:
    label: str
    detail: str
    path: str
    open_now: bool = False


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def user_name() -> str:
    return str(read_json(st.CONFIG_DIR / "config.json").get("name") or getpass.getuser().capitalize())


def greeting(now: Optional[float] = None) -> str:
    h = time.localtime(now).tm_hour
    part = "morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 18 else "evening" if 18 <= h < 23 else "night"
    return f"Good {part}, {user_name()}."


def stats() -> list[tuple[str, str]]:
    settings = read_json(CLAUDE_DIR / "settings.json")
    skills = [p for p in (CLAUDE_DIR / "skills").glob("*") if (p / "SKILL.md").is_file()] if (CLAUDE_DIR / "skills").is_dir() else []
    plugins = [k for k, v in (settings.get("enabledPlugins") or {}).items() if v]
    mcp = read_json(Path.home() / ".claude.json").get("mcpServers") or {}
    return [("model", str(settings.get("model", "default"))), ("skills", str(len(skills))),
            ("plugins", str(len(plugins))), ("mcp", str(len(mcp)))]


def tools_present() -> list[tuple[str, bool]]:
    return [(n, shutil.which(n) is not None) for n in ("lynx", "ghidra", "tmux", "git")]


def continue_entries() -> list[Entry]:
    """Tabs that are open now plus the ones remembered from last time."""
    live = [w for w in list_windows() if w.kind == "work"]
    entries = [Entry(w.name, w.cwd.replace(str(Path.home()), "~"), w.cwd, True) for w in live]
    open_paths = {w.cwd for w in live}
    for t in st.load().tabs:
        if t.cwd not in open_paths:
            entries.append(Entry(t.name, t.cwd.replace(str(Path.home()), "~"), t.cwd))
    return entries


def project_entries(limit: int) -> list[Entry]:
    out: list[Entry] = []
    for p in discover(limit):
        age = fmt_age(max(0.0, time.time() - p.active)) if p.active else "-"
        out.append(Entry(p.name, f"{p.branch or '-'}  {age}", p.path))
    return out


def open_entry(entry: Entry) -> None:
    if entry.open_now:
        for w in list_windows():
            if w.kind == "work" and w.cwd == entry.path:
                run("select-window", "-t", w.id)
                return
    subprocess.run([BIN, "new", entry.path], capture_output=True, check=False)


Handler = Callable[[MouseEvent], None]


def clicker(entry: Entry) -> Handler:
    def handle(event: MouseEvent) -> None:
        if event.event_type == MouseEventType.MOUSE_UP:
            open_entry(entry)
    return handle


def cell(entry: Entry, index: int, selected: int, width: int) -> StyleAndTextTuples:
    on = index == selected
    mark = "●" if entry.open_now else "○" if entry.open_now is False and index >= 0 else " "
    left = clip(f" {mark} {entry.label}", max(8, width - 2 - min(len(entry.detail), width // 2)))
    right = clip(entry.detail, max(0, width - len(left) - 1))
    pad = " " * max(1, width - len(left) - len(right))
    h = clicker(entry)
    base = "class:sel" if on else ""
    return [(base or ("class:accent" if entry.open_now else "class:read"), left, h),
            (base or "", pad, h), (base or "class:muted", right, h)]


def centered(line: StyleAndTextTuples, text_len: int, cols: int) -> StyleAndTextTuples:
    return [("", " " * max(0, (cols - text_len) // 2))] + line


class Welcome:
    def __init__(self) -> None:
        self.selected = 0
        self.word = random.choice(WORDS)
        self.refreshed = 0.0
        self.cont: list[Entry] = []
        self.proj: list[Entry] = []

    def reload(self) -> None:
        if time.time() - self.refreshed < 2:
            return
        self.refreshed = time.time()
        self.cont = continue_entries()
        self.proj = project_entries(14)

    def entries(self) -> list[Entry]:
        return self.cont + self.proj

    def render(self) -> StyleAndTextTuples:
        self.reload()
        size = get_app().output.get_size()
        cols, rows = size.columns, size.rows
        out: StyleAndTextTuples = []

        def nl() -> None:
            out.append(("", "\n"))

        if rows >= 24 and cols >= 56:
            for line in wordmark():
                out.extend(centered(gradient(line, AURELIA.search, AURELIA.write), len(line), cols))
                nl()
        else:
            title = "◆ A T E L I E R"
            out.extend(centered([("class:title", title)], len(title), cols))
            nl()
        sub = f"{greeting()}  {self.word}…"
        nl()
        out.extend(centered([("class:read", sub)], len(sub), cols))
        nl()
        info = "  ".join(f"{k} {v}" for k, v in stats())
        out.extend(centered([("class:muted", info)], len(info), cols))
        nl()
        tools = "  ".join(f"{n} {'✓' if ok else '·'}" for n, ok in tools_present())
        out.extend(centered([("class:muted", tools)], len(tools), cols))
        nl()
        nl()

        width = min(cols - 4, 100)
        pad = " " * max(0, (cols - width) // 2)
        entries = self.entries()
        self.selected = max(0, min(self.selected, len(entries) - 1)) if entries else 0
        n_cont = len(self.cont)

        def heading(text: str) -> StyleAndTextTuples:
            return [("class:title", text)]

        two_col = width >= 84
        if two_col:
            half = (width - 3) // 2
            left_rows = [heading(" Continue")] + [cell(e, i, self.selected, half) for i, e in enumerate(self.cont)]
            right_rows = [heading(" Projects")] + [cell(e, n_cont + i, self.selected, half) for i, e in enumerate(self.proj)]
            height = min(max(len(left_rows), len(right_rows)), max(4, rows - 14))
            for r in range(height):
                out.append(("", pad))
                for rowset in (left_rows, right_rows):
                    frag = rowset[r] if r < len(rowset) else []
                    used = sum(len(t) for _, t, *_ in frag)
                    out.extend(frag)
                    out.append(("", " " * max(0, half - used)))
                    if rowset is left_rows:
                        out.append(("class:border", " │ "))
                nl()
        else:
            budget = max(4, rows - 14)
            out.append(("", pad))
            out.append(("class:title", " Continue"))
            nl()
            for i, e in enumerate(self.cont[: budget // 2]):
                out.append(("", pad))
                out.extend(cell(e, i, self.selected, width))
                nl()
            out.append(("", pad))
            out.append(("class:title", " Projects"))
            nl()
            for i, e in enumerate(self.proj[: max(2, budget // 2)]):
                out.append(("", pad))
                out.extend(cell(e, n_cont + i, self.selected, width))
                nl()
        nl()
        hint = "click or ↑↓ Enter to open   F3 new tab   F2 system   F7 manage   F4 rename   F5/F6 panels"
        out.extend(centered([("class:muted", clip(hint, cols - 2))], min(len(hint), cols - 2), cols))
        return out


def main() -> int:
    w = Welcome()
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(_: KeyPressEvent) -> None:
        w.selected = max(0, w.selected - 1)

    @kb.add("down")
    @kb.add("j")
    def _down(_: KeyPressEvent) -> None:
        w.selected = min(max(0, len(w.entries()) - 1), w.selected + 1)

    @kb.add("enter")
    def _open(_: KeyPressEvent) -> None:
        e = w.entries()
        if e:
            open_entry(e[w.selected])

    app: Application[None] = Application(
        layout=Layout(Window(FormattedTextControl(w.render, focusable=True), wrap_lines=False)),
        key_bindings=kb, style=style(), full_screen=True, mouse_support=True, refresh_interval=2.0)
    app.run()
    return 0


def pick_main() -> int:
    """Popup picker for a new tab: type to filter, Enter opens, Esc closes; a typed existing path works too."""
    projects: list[Project] = discover(200)
    query = ""
    selected = 0

    def shown() -> list[Project]:
        q = query.lower()
        return [p for p in projects if q in p.name.lower()] if q else projects

    def open_and_exit(path: str) -> None:
        subprocess.run([BIN, "new", path], capture_output=True, check=False)
        get_app().exit()

    def render() -> StyleAndTextTuples:
        out: StyleAndTextTuples = [("class:title", " New tab"), ("class:muted", "  type to filter, Enter opens, Esc closes\n"),
                                   ("class:accent", f" ❯ {query}"), ("class:muted", "▏\n\n")]
        size = get_app().output.get_size()
        items = shown()
        top = max(0, min(selected - (size.rows - 6) // 2, max(0, len(items) - (size.rows - 5))))
        for i, p in enumerate(items[top: top + max(3, size.rows - 5)], start=top):
            line = clip(f" {p.name}", 34).ljust(34) + clip(f" {p.branch or '-'}", 20)
            h = _pick_handler(p.path)
            out.append(("class:sel" if i == selected else "class:read", line + "\n", h))
        if not items:
            out.append(("class:muted", f" nothing matches; Enter opens '{query}' if it is a folder\n"))
        return out

    def _pick_handler(path: str) -> Handler:
        def handle(event: MouseEvent) -> None:
            if event.event_type == MouseEventType.MOUSE_UP:
                open_and_exit(path)
        return handle

    kb = KeyBindings()

    @kb.add("up")
    def _up(_: KeyPressEvent) -> None:
        nonlocal selected
        selected = max(0, selected - 1)

    @kb.add("down")
    def _down(_: KeyPressEvent) -> None:
        nonlocal selected
        selected = min(max(0, len(shown()) - 1), selected + 1)

    @kb.add("enter")
    def _enter(_: KeyPressEvent) -> None:
        items = shown()
        if items:
            open_and_exit(items[selected].path)
        elif query and Path(query).expanduser().is_dir():
            open_and_exit(str(Path(query).expanduser().resolve()))

    @kb.add("escape")
    @kb.add("c-c")
    def _esc(event: KeyPressEvent) -> None:
        event.app.exit()

    @kb.add("backspace")
    def _bs(_: KeyPressEvent) -> None:
        nonlocal query, selected
        query, selected = query[:-1], 0

    @kb.add("<any>")
    def _type(event: KeyPressEvent) -> None:
        nonlocal query, selected
        data = event.key_sequence[0].data
        if len(data) == 1 and data.isprintable():
            query, selected = query + data, 0

    app: Application[None] = Application(
        layout=Layout(Window(FormattedTextControl(render, focusable=True), wrap_lines=False)),
        key_bindings=kb, style=style(), full_screen=True, mouse_support=True)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
