"""Dash panel: current model, token usage and a live tool-activity feed for one work tab.

Data comes from the Claude Code transcript (~/.claude/projects/<enc>/<session>.jsonl), read incrementally.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

from atelier.theme import clip, fmt_age, style

TMUX_SOCKET = os.environ.get("ATELIER_SOCKET", "atelier")
MAX_ACTIVITY = 300
MODEL_ALIASES: tuple[str, ...] = ("opus", "sonnet", "haiku")
STATUS_TTL = 8.0
READ_CHUNK = 4 * 1024 * 1024

Frag = tuple[str, str] | tuple[str, str, Callable[[MouseEvent], object]]
Frags = list[Frag]

READ_TOOLS = frozenset({"Read", "Grep", "Glob", "LS"})
WRITE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})
WEB_TOOLS = frozenset({"WebFetch", "WebSearch"})
AGENT_TOOLS = frozenset({"Agent", "Task"})


# ---------------------------------------------------------------- pure helpers

def encode_project_dir(cwd: str) -> str:
    """Claude Code's transcript directory name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def classify_tool(name: str) -> str:
    """Style class (theme vocabulary) for a tool name."""
    if name.startswith("mcp__"):
        return "mcp"
    if name in READ_TOOLS:
        return "read"
    if name in WRITE_TOOLS:
        return "write"
    if name == "Bash":
        return "run"
    if name in WEB_TOOLS:
        return "web"
    if name in AGENT_TOOLS:
        return "search"
    return "other"


def display_name(name: str) -> str:
    """Short tool label: the last segment of an mcp__server__tool name."""
    if name.startswith("mcp__"):
        return name.rsplit("__", 1)[-1] or name
    return name


def fmt_tokens(n: int) -> str:
    """Compact count: 950, 12.3k, 1.2M, 3.4B."""
    if n < 1000:
        return str(max(n, 0))
    value = float(n)
    for suffix in ("k", "M", "B"):
        value /= 1000.0
        if round(value, 1) < 1000.0 or suffix == "B":
            return f"{value:.1f}{suffix}"
    return f"{value:.1f}B"


def rel_path(path: str, cwd: str) -> str:
    """Path relative to cwd when inside it, ~-shortened under home, else unchanged."""
    if not path:
        return ""
    if not os.path.isabs(path):
        return path
    try:
        rel = os.path.relpath(path, cwd) if cwd else path
    except ValueError:
        rel = path
    if cwd and not rel.startswith(".."):
        return rel
    home = str(Path.home())
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def summarize_tool(name: str, tool_input: dict[str, object], cwd: str) -> str:
    """One-line description of what a tool call does."""
    if name == "Bash":
        return first_line(_str(tool_input.get("command")))
    if name in AGENT_TOOLS:
        return _str(tool_input.get("description")) or first_line(_str(tool_input.get("prompt")))
    if name == "Skill":
        return _str(tool_input.get("skill")) or _str(tool_input.get("args"))
    if name in WEB_TOOLS:
        return _str(tool_input.get("url")) or _str(tool_input.get("query"))
    if name == "Glob":
        return _str(tool_input.get("pattern"))
    if name == "Grep":
        pattern = _str(tool_input.get("pattern"))
        where = rel_path(_str(tool_input.get("path")), cwd)
        return f"{pattern} {where}".strip()
    if name in READ_TOOLS or name in WRITE_TOOLS:
        raw = _str(tool_input.get("file_path")) or _str(tool_input.get("notebook_path")) or _str(tool_input.get("path"))
        return rel_path(raw, cwd)
    for value in tool_input.values():
        if isinstance(value, str) and value.strip():
            return first_line(value)
    return ""


def bash_tags(command: str) -> str:
    low = command.lower()
    tags: list[str] = []
    if "tbrowse" in low or "lynx" in low:
        tags.append("[text-web]")
    if "analyzeheadless" in low or "ghidra" in low:
        tags.append("[ghidra]")
    return " ".join(tags)


def parse_timestamp(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def pick_session(project_dir: Path, session_id: str) -> Path | None:
    """The session transcript: the named one when it exists, else the newest *.jsonl."""
    if session_id:
        named = project_dir / f"{session_id}.jsonl"
        if named.is_file():
            return named
    best: Path | None = None
    best_mtime = -1.0
    try:
        for entry in project_dir.glob("*.jsonl"):
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime > best_mtime:
                best, best_mtime = entry, mtime
    except OSError:
        return None
    return best


# ------------------------------------------------------------------ state

@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_create: int = 0

    @property
    def context(self) -> int:
        return self.input + self.cache_read + self.cache_create


@dataclass
class Activity:
    tool_id: str
    ts: float | None
    name: str
    summary: str
    tags: str
    status: str = "run"  # run | ok | err


@dataclass
class DashState:
    cwd: str = ""
    model: str = ""
    last_usage: Usage | None = None
    last_ts: float | None = None
    totals: Usage = field(default_factory=Usage)
    activity: list[Activity] = field(default_factory=list)
    _usage_by_msg: dict[str, Usage] = field(default_factory=dict)
    _pending: dict[str, Activity] = field(default_factory=dict)

    def feed(self, lines: Iterable[str | bytes]) -> int:
        """Consume transcript lines; returns how many activity entries were added."""
        before = 0
        added = 0
        for raw in lines:
            if not raw or not raw.strip():
                continue
            try:
                obj: object = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(obj, dict):
                continue
            added += self._line(obj)
            before += 1
        return added

    def _line(self, obj: dict[str, object]) -> int:
        kind = obj.get("type")
        message = obj.get("message")
        if not isinstance(message, dict) or kind not in ("assistant", "user"):
            return 0
        content = message.get("content")
        if kind == "user":
            self._results(content)
            return 0
        ts = parse_timestamp(obj.get("timestamp"))
        model = message.get("model")
        if isinstance(model, str) and model and not model.startswith("<"):
            self.model = model
            if ts is not None:
                self.last_ts = ts
            self._usage(message, _str(message.get("id")) or _str(obj.get("uuid")))
        return self._tools(content, ts)

    def _usage(self, message: dict[str, object], msg_id: str) -> None:
        raw = message.get("usage")
        if not isinstance(raw, dict):
            return

        def num(key: str) -> int:
            v = raw.get(key)
            return v if isinstance(v, int) and not isinstance(v, bool) else 0

        usage = Usage(num("input_tokens"), num("output_tokens"), num("cache_read_input_tokens"), num("cache_creation_input_tokens"))
        # one API message is written as several lines (one per content block) that repeat the same usage
        old = self._usage_by_msg.get(msg_id) if msg_id else None
        if old is not None:
            self.totals.input -= old.input
            self.totals.output -= old.output
            self.totals.cache_read -= old.cache_read
            self.totals.cache_create -= old.cache_create
        if msg_id:
            self._usage_by_msg[msg_id] = usage
        self.totals.input += usage.input
        self.totals.output += usage.output
        self.totals.cache_read += usage.cache_read
        self.totals.cache_create += usage.cache_create
        self.last_usage = usage

    def _tools(self, content: object, ts: float | None) -> int:
        if not isinstance(content, list):
            return 0
        added = 0
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = _str(block.get("name"))
            raw_input = block.get("input")
            tool_input: dict[str, object] = raw_input if isinstance(raw_input, dict) else {}
            tags = bash_tags(_str(tool_input.get("command"))) if name == "Bash" else ""
            entry = Activity(_str(block.get("id")), ts, name, summarize_tool(name, tool_input, self.cwd), tags)
            self.activity.append(entry)
            if entry.tool_id:
                self._pending[entry.tool_id] = entry
            added += 1
        if len(self.activity) > MAX_ACTIVITY:
            for dropped in self.activity[: len(self.activity) - MAX_ACTIVITY]:
                self._pending.pop(dropped.tool_id, None)
            del self.activity[: len(self.activity) - MAX_ACTIVITY]
        return added

    def _results(self, content: object) -> None:
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            entry = self._pending.pop(_str(block.get("tool_use_id")), None)
            if entry is not None:
                entry.status = "err" if block.get("is_error") is True else "ok"


class Tracker:
    """Reads one transcript incrementally and keeps a DashState; resets on file change or truncation."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.path: Path | None = None
        self.inode = -1
        self.pos = 0
        self.leftover = b""
        self.state = DashState(cwd=cwd)

    def poll(self, path: Path | None, cwd: str) -> int:
        """Pull appended bytes into the state; returns new activity entries (0 after a reset)."""
        if path is None:
            if self.path is not None or cwd != self.cwd:
                self._reset(None, cwd)
            return 0
        try:
            st = path.stat()
        except OSError:
            return 0
        if path != self.path or st.st_ino != self.inode or st.st_size < self.pos or cwd != self.cwd:
            self._reset(path, cwd)
            self.inode = st.st_ino
            return self._read(path, DashState(cwd=cwd), swap=True)
        if st.st_size == self.pos:
            return 0
        return self._read(path, self.state, swap=False)

    def _reset(self, path: Path | None, cwd: str) -> None:
        self.path = path
        self.cwd = cwd
        self.pos = 0
        self.leftover = b""
        self.inode = -1
        self.state = DashState(cwd=cwd)

    def _read(self, path: Path, target: DashState, swap: bool) -> int:
        added = 0
        try:
            with path.open("rb") as fh:
                fh.seek(self.pos)
                while True:
                    chunk = fh.read(READ_CHUNK)
                    if not chunk:
                        break
                    self.pos += len(chunk)
                    parts = (self.leftover + chunk).split(b"\n")
                    self.leftover = parts.pop()
                    added += target.feed(parts)
        except OSError:
            return 0
        if swap:
            self.state = target
            return 0
        return added


# ---------------------------------------------------------------- tmux I/O

def run_tmux(args: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(["tmux", "-L", TMUX_SOCKET, *args], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return proc.returncode == 0, proc.stdout.strip()


@dataclass(frozen=True)
class Context:
    cwd: str
    pane: str
    session_id: str
    error: str


def resolve_context() -> Context:
    win = os.environ.get("ATELIER_WIN", "")
    cwd = os.environ.get("ATELIER_CWD", "")
    pane = ""
    session_id = ""
    if win:
        if not cwd:
            ok, out = run_tmux(["show-options", "-wqv", "-t", win, "@cwd"])
            cwd = out if ok else ""
        ok, out = run_tmux(["list-panes", "-t", win, "-F", "#{pane_id} #{@role}"])
        if ok:
            for line in out.splitlines():
                bits = line.split()
                if len(bits) == 2 and bits[1] == "claude":
                    pane = bits[0]
                    break
        ok, out = run_tmux(["show-options", "-wqv", "-t", win, "@session"])
        session_id = out if ok else ""
    error = "" if cwd else "no ATELIER_WIN / @cwd"
    return Context(cwd, pane, session_id, error)


def send_model(pane: str, alias: str) -> str:
    if not pane:
        return "no claude pane found"
    ok1, _ = run_tmux(["send-keys", "-t", pane, "-l", f"/model {alias}"])
    ok2, _ = run_tmux(["send-keys", "-t", pane, "Enter"])
    return f"sent /model {alias}" if ok1 and ok2 else f"send to {pane} failed"


# ---------------------------------------------------------------- rendering

def _pad(text: str, width: int) -> str:
    return clip(text, width).ljust(width)


def build_header(state: DashState, width: int, now: float, status: str, on_model: Callable[[str], None]) -> Frags:
    """Model and usage blocks as fragments; lines are separated by newline fragments, no trailing newline."""
    out: Frags = []
    model = state.model or "-"
    out += [("class:title", "model "), ("class:accent", clip(model, max(0, width - 6))), ("", "\n")]

    def handler(alias: str) -> Callable[[MouseEvent], object]:
        def inner(event: MouseEvent) -> object:
            if event.event_type == MouseEventType.MOUSE_UP:
                on_model(alias)
                return None
            return NotImplemented
        return inner

    for alias in MODEL_ALIASES:
        on = alias in state.model.lower()
        out.append(("class:btn.on" if on else "class:btn", f" {alias} ", handler(alias)))
        out.append(("", " "))
    if status:
        out += [("", "\n"), ("class:warn", clip(status, width))]

    last = state.last_usage
    age = f"last reply {fmt_age(max(0.0, now - state.last_ts))} ago" if state.last_ts is not None else "no reply yet"
    out += [("", "\n"), ("class:title", "usage "), ("class:muted", clip(age, max(0, width - 6))), ("", "\n")]
    out += [("class:muted", "ctx "), ("class:fg", fmt_tokens(last.context) if last else "-"),
            ("class:muted", "  out "), ("class:fg", fmt_tokens(state.totals.output)), ("", "\n")]
    out += [("class:muted", "cache r "), ("class:fg", fmt_tokens(state.totals.cache_read)),
            ("class:muted", " w "), ("class:fg", fmt_tokens(state.totals.cache_create)),
            ("class:muted", " in "), ("class:fg", fmt_tokens(state.totals.input))]
    return out


def activity_row(entry: Activity, width: int, zebra: bool) -> Frags:
    """One feed row: `HH:MM mark tool [tag] summary`, padded to width."""
    cls = classify_tool(entry.name)
    bg = " class:zebra" if zebra else ""
    stamp = datetime.fromtimestamp(entry.ts).strftime("%H:%M") if entry.ts is not None else "--:--"
    if entry.status == "err":
        mark, mark_cls = "! ", "class:err"
    elif entry.status == "ok":
        mark, mark_cls = "ok", "class:dim"
    else:
        mark, mark_cls = "… ", "class:warn"
    name = clip(display_name(entry.name), 8)
    used = len(stamp) + 1 + 2 + 1 + len(name) + 1
    tag = entry.tags
    room = max(0, width - used)
    if tag and room > len(tag) + 4:
        room_summary = room - len(tag) - 1
        summary = clip(entry.summary, room_summary)
        text_tail = summary + " " + tag
    else:
        tag = ""
        text_tail = clip(entry.summary, room)
    summary_part = text_tail[: len(text_tail) - len(tag)] if tag else text_tail
    row: Frags = [
        (f"class:muted{bg}", stamp + " "),
        (mark_cls + bg, mark + " "),
        (f"class:{cls}{bg}", name + " "),
        (f"class:{cls}{bg}", summary_part),
    ]
    if tag:
        row.append((f"class:accent{bg}", tag))
    row.append((f"class:{cls}{bg}", " " * max(0, width - used - len(text_tail))))
    return row


def build_feed(state: DashState, width: int, height: int, offset: int,
               on_scroll: Callable[[int], None]) -> tuple[Frags, int]:
    """Feed title plus newest-first rows. Returns fragments and the clamped scroll offset."""
    rows = max(0, height - 1)
    total = len(state.activity)
    offset = max(0, min(offset, max(0, total - rows)))

    def scroll_handler(event: MouseEvent) -> object:
        if event.event_type == MouseEventType.SCROLL_UP:
            on_scroll(-3)
            return None
        if event.event_type == MouseEventType.SCROLL_DOWN:
            on_scroll(3)
            return None
        return NotImplemented

    title = f"activity {total}" + (f"  +{offset} newer" if offset else "")
    out: Frags = [("class:title", clip(title, width))]
    newest_first = state.activity[::-1]
    visible = newest_first[offset: offset + rows]
    for i, entry in enumerate(visible):
        out.append(("", "\n"))
        for frag in activity_row(entry, width, (offset + i) % 2 == 1):
            out.append((frag[0], frag[1], scroll_handler))
    if not visible:
        out += [("", "\n"), ("class:muted", clip("no tool calls yet", width), scroll_handler)]
    for _ in range(max(0, rows - len(visible) - (0 if visible else 1))):
        out.append(("", "\n"))
        out.append(("", " " * width, scroll_handler))
    return out, offset


# ---------------------------------------------------------------- application

class DashApp:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tracker = Tracker("")
        self.pane = ""
        self.error = ""
        self.offset = 0
        self.status = ""
        self.status_at = 0.0
        self.feed_window: Window | None = None

    def poll_once(self) -> None:
        ctx = resolve_context()
        path: Path | None = None
        if ctx.cwd:
            project_dir = Path.home() / ".claude" / "projects" / encode_project_dir(ctx.cwd)
            path = pick_session(project_dir, ctx.session_id)
        with self.lock:
            self.pane = ctx.pane
            self.error = ctx.error
        added = self.tracker.poll(path, ctx.cwd)
        if added:
            with self.lock:
                if self.offset > 0:
                    self.offset += added

    def poll_loop(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:  # keep the panel alive whatever the transcript holds
                pass
            time.sleep(1.0)

    def on_model(self, alias: str) -> None:
        with self.lock:
            pane = self.pane
        message = send_model(pane, alias)
        with self.lock:
            self.status = message
            self.status_at = time.monotonic()

    def feed_height(self) -> int:
        win = self.feed_window
        info = win.render_info if win is not None else None
        return info.window_height if info is not None else 8

    def scroll(self, delta: int) -> None:
        with self.lock:
            total = len(self.tracker.state.activity)
            rows = max(0, self.feed_height() - 1)
            self.offset = max(0, min(self.offset + delta, max(0, total - rows)))

    def header_fragments(self) -> Frags:
        width = get_app().output.get_size().columns
        with self.lock:
            status = self.status if time.monotonic() - self.status_at < STATUS_TTL else ""
            if self.error and not status:
                status = self.error
            return build_header(self.tracker.state, width, time.time(), status, self.on_model)

    def header_height(self) -> int:
        return sum(f[1].count("\n") for f in self.header_fragments()) + 1

    def feed_fragments(self) -> Frags:
        width = get_app().output.get_size().columns
        height = self.feed_height()
        with self.lock:
            frags, self.offset = build_feed(self.tracker.state, width, height, self.offset, self.scroll)
        return frags


def main() -> int:
    dash = DashApp()
    header = Window(FormattedTextControl(dash.header_fragments), height=lambda: Dimension.exact(dash.header_height()), wrap_lines=False)
    feed = Window(FormattedTextControl(dash.feed_fragments), wrap_lines=False)
    dash.feed_window = feed

    kb = KeyBindings()

    def bind(keys: tuple[str, ...], delta: Callable[[], int]) -> None:
        @kb.add(*keys)
        def _(event: KeyPressEvent) -> None:
            dash.scroll(delta())

    bind(("j", "down"), lambda: 1)
    bind(("k", "up"), lambda: -1)
    bind(("pagedown", "space"), lambda: max(1, dash.feed_height() - 2))
    bind(("pageup",), lambda: -max(1, dash.feed_height() - 2))
    bind(("g", "home"), lambda: -10**9)
    bind(("G", "end"), lambda: 10**9)

    app: Application[None] = Application(
        layout=Layout(HSplit([header, feed])),
        key_bindings=kb,
        style=style(),
        full_screen=True,
        mouse_support=True,
        refresh_interval=1.0,
    )
    threading.Thread(target=dash.poll_loop, daemon=True).start()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
