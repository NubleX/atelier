"""Work tree panel: git-aware file tree beside Claude. Click a folder to fold it; double-click or Enter on a
file puts `@path` into Claude's prompt. Files touched in the last half minute carry a pink diamond."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

from atelier.theme import clip, style
from atelier.tmuxctl import claude_pane, get_option, send_text

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target", ".cache", ".pytest_cache"}
RECENT_SECONDS = 30
DOUBLE_CLICK = 0.45
GIT_COLORS = {"M": "class:warn", "A": "class:ok", "?": "class:ok", "D": "class:err", "R": "class:run", "U": "class:err"}


@dataclass(frozen=True)
class Row:
    path: Path
    depth: int
    is_dir: bool
    open: bool


def git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""


def git_status(root: Path) -> dict[str, str]:
    """Relative path -> one-letter status ('M', '?', ...), plus every parent folder marked '*'."""
    out: dict[str, str] = {}
    for rec in git(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal").split("\0"):
        if len(rec) < 4:
            continue
        code = (rec[1] if rec[1] != " " else rec[0]).strip() or "M"
        rel = rec[3:].rstrip("/")
        out[rel] = code
        parent = Path(rel).parent
        while str(parent) not in ("", "."):
            out.setdefault(str(parent), "*")
            parent = parent.parent
    return out


def branch_line(root: Path) -> str:
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if not branch:
        return "not a git repo"
    trees = git(root, "worktree", "list", "--porcelain").count("worktree ")
    return f"{branch}" + (f"  · {trees} worktrees" if trees > 1 else "")


def list_dir(path: Path) -> list[Path]:
    try:
        entries = [p for p in path.iterdir() if p.name not in SKIP]
    except OSError:
        return []
    return sorted(entries, key=lambda p: (not p.is_dir(), p.name.lower()))


def build_rows(root: Path, expanded: set[Path]) -> list[Row]:
    rows: list[Row] = []

    def walk(d: Path, depth: int) -> None:
        for p in list_dir(d):
            is_dir = p.is_dir()
            is_open = is_dir and p in expanded
            rows.append(Row(p, depth, is_dir, is_open))
            if is_open:
                walk(p, depth + 1)

    walk(root, 0)
    return rows


def resolve_root() -> Path:
    override = os.environ.get("ATELIER_CWD")
    if override:
        return Path(override)
    win = os.environ.get("ATELIER_WIN", "")
    return Path(get_option(win, "@cwd") or os.getcwd()) if win else Path(os.getcwd())


class Tree:
    def __init__(self) -> None:
        self.root = resolve_root()
        self.expanded: set[Path] = set()
        self.cursor = 0
        self.top = 0
        self.status: dict[str, str] = {}
        self.branch = ""
        self.refreshed = 0.0
        self.last_click: tuple[int, float] = (-1, 0.0)
        self.message = ""
        self.rows: list[Row] = []

    def refresh(self, force: bool = False) -> None:
        if not force and time.time() - self.refreshed < 2:
            return
        self.refreshed = time.time()
        self.status = git_status(self.root)
        self.branch = branch_line(self.root)
        self.rows = build_rows(self.root, self.expanded)

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root))
        except ValueError:
            return str(p)

    def activate(self, index: int) -> None:
        if not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        self.cursor = index
        if row.is_dir:
            self.expanded.symmetric_difference_update({row.path})
            self.refresh(force=True)
        else:
            self.insert(row)

    def insert(self, row: Row) -> None:
        win = os.environ.get("ATELIER_WIN", "")
        pane = claude_pane(win) if win else None
        if pane:
            send_text(pane, f"@{self.rel(row.path)} ")
            self.message = f"added @{self.rel(row.path)}"
        else:
            self.message = "no Claude pane in this tab"

    def click(self, index: int, event: MouseEvent) -> None:
        if event.event_type != MouseEventType.MOUSE_UP:
            return
        now = time.time()
        prev_index, prev_time = self.last_click
        self.last_click = (index, now)
        if not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        if row.is_dir:
            self.activate(index)
        elif prev_index == index and now - prev_time < DOUBLE_CLICK:
            self.insert(row)
        else:
            self.cursor = index

    def render(self) -> StyleAndTextTuples:
        self.refresh()
        size = get_app().output.get_size()
        cols, height = size.columns, max(3, size.rows - 3)
        out: StyleAndTextTuples = [("class:title", clip(f" ◆ {self.root.name}", cols)), ("", "\n"),
                                   ("class:muted", clip(f"   {self.branch}", cols)), ("", "\n")]
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
        self.top = max(0, min(self.top, max(0, len(self.rows) - height)))
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + height:
            self.top = self.cursor - height + 1
        now = time.time()
        for i in range(self.top, min(len(self.rows), self.top + height)):
            row = self.rows[i]
            rel = self.rel(row.path)
            code = self.status.get(rel, "")
            recent = False
            if not row.is_dir:
                try:
                    recent = now - row.path.stat().st_mtime < RECENT_SECONDS
                except OSError:
                    pass
            icon = ("▾ " if row.open else "▸ ") if row.is_dir else "  "
            label = clip(f"{'  ' * row.depth}{icon}{row.path.name}", cols - 3)
            name_style = "class:sel" if i == self.cursor else ("class:run" if row.is_dir else GIT_COLORS.get(code, "class:read"))
            mark_style = "class:sel" if i == self.cursor else ("class:write" if recent else GIT_COLORS.get(code, "class:muted"))
            mark = "◆" if recent else (code if code and code != "*" else ("·" if code == "*" else " "))

            def handler(index: int = i) -> Callable[[MouseEvent], None]:
                return lambda event: self.click(index, event)

            out.append((name_style, label.ljust(cols - 2), handler()))
            out.append((mark_style, f"{mark} ", handler()))
            out.append(("", "\n"))
        if self.message:
            out.append(("class:muted", clip(" " + self.message, cols)))
        return out


def main() -> int:
    tree = Tree()
    tree.refresh(force=True)
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(_: KeyPressEvent) -> None:
        tree.cursor = max(0, tree.cursor - 1)

    @kb.add("down")
    @kb.add("j")
    def _down(_: KeyPressEvent) -> None:
        tree.cursor = min(len(tree.rows) - 1, tree.cursor + 1)

    @kb.add("enter")
    @kb.add("space")
    @kb.add("right")
    def _go(_: KeyPressEvent) -> None:
        tree.activate(tree.cursor)

    @kb.add("left")
    def _fold(_: KeyPressEvent) -> None:
        if 0 <= tree.cursor < len(tree.rows) and tree.rows[tree.cursor].open:
            tree.activate(tree.cursor)

    @kb.add("r")
    def _refresh(_: KeyPressEvent) -> None:
        tree.refresh(force=True)

    app: Application[None] = Application(
        layout=Layout(Window(FormattedTextControl(tree.render, focusable=True), wrap_lines=False)),
        key_bindings=kb, style=style(), full_screen=True, mouse_support=True, refresh_interval=2.0)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
