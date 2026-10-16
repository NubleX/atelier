"""System panel: processes, network and disk views in one prompt_toolkit full-screen app.

Run with `atelier panel system`. Deletion is limited to an explicit allowlist (see `safe_targets`).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional, Union, cast

import psutil
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

from atelier.theme import Fragments, clip, fmt_size, style

SortKey = Literal["cpu", "mem", "pid", "name"]
Hit = Callable[[int], None]
Row = tuple[Fragments, Optional[Hit]]
MFragments = list[Union[tuple[str, str], tuple[str, str, Callable[[MouseEvent], None]]]]

VIEW_NAMES = ("Processes", "Network", "Disk")
REFRESH_SECONDS = 2.0
LSOF_EVERY = 10.0
TRANSCRIPT_DAYS = 60
TRANSCRIPT_NOTE = "deja searches these - history is lost"


# --------------------------------------------------------------------------- data rows

@dataclass(frozen=True)
class ProcRow:
    pid: int
    user: str
    cpu: float
    mem: float
    rss: int
    name: str
    cmd: str


@dataclass(frozen=True)
class NetRow:
    proto: str
    local: str
    remote: str
    status: str
    pid: Optional[int]
    pname: str


@dataclass(frozen=True)
class Mount:
    mount: str
    fstype: str
    total: int
    used: int
    free: int
    percent: float


@dataclass(frozen=True)
class OpenDeleted:
    pid: int
    command: str
    size: int
    name: str


@dataclass
class Candidate:
    label: str
    roots: tuple[Path, ...]
    shown: str
    note: str
    kind: Literal["dirs", "transcripts"] = "dirs"
    size: Optional[int] = None
    selected: bool = False
    danger: bool = False


@dataclass(frozen=True)
class DeleteResult:
    freed: int = 0
    errors: int = 0


@dataclass
class Confirm:
    prompt: str
    action: Callable[[], None]


@dataclass
class Page:
    header: list[Row] = field(default_factory=list)
    body: list[Row] = field(default_factory=list)
    focus: Optional[int] = None


# --------------------------------------------------------------------------- pure helpers

def _as_float(v: object) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def _as_str(v: object, default: str = "") -> str:
    return v if isinstance(v, str) else default


def _clean(s: str) -> str:
    return "".join(ch if ch.isprintable() else " " for ch in s)


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def sort_procs(rows: Sequence[ProcRow], key: SortKey) -> list[ProcRow]:
    if key == "mem":
        return sorted(rows, key=lambda r: (-r.mem, -r.cpu, r.pid))
    if key == "pid":
        return sorted(rows, key=lambda r: r.pid)
    if key == "name":
        return sorted(rows, key=lambda r: (r.name.lower(), r.pid))
    return sorted(rows, key=lambda r: (-r.cpu, -r.mem, r.pid))


def sort_net(rows: Sequence[NetRow]) -> list[NetRow]:
    def port(r: NetRow) -> int:
        tail = r.local.rsplit(":", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    return sorted(rows, key=lambda r: (r.status != "LISTEN", r.proto, port(r), r.remote))


def is_listening(r: NetRow) -> bool:
    return r.status == "LISTEN" or (r.proto.startswith("udp") and r.remote in ("", "*"))


def fmt_rate(n: float) -> str:
    return f"{fmt_size(n)}/s"


def fmt_addr(addr: Sequence[object]) -> str:
    if len(addr) < 2:
        return "*"
    ip, port = str(addr[0]), str(addr[1])
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def shorten_home(path: Union[str, Path], home: Path) -> str:
    p = str(path)
    h = str(home)
    if p == h:
        return "~"
    return "~" + p[len(h):] if p.startswith(h + os.sep) else p


def usage_class(pct: float) -> str:
    if pct >= 90:
        return "err"
    if pct >= 70:
        return "warn"
    return "ok"


def usage_bar(pct: float, width: int) -> tuple[str, str]:
    """(filled, empty) block-char strings that together are `width` cells wide."""
    width = max(0, width)
    filled = clamp(round(max(0.0, min(100.0, pct)) / 100 * width), 0, width)
    return "█" * filled, "░" * (width - filled)


def fit_frags(frags: Fragments, width: int) -> Fragments:
    """Cuts fragments to exactly `width` cells, padding with the last style."""
    out: Fragments = []
    left = width
    last = ""
    for st, text in frags:
        if left <= 0:
            break
        piece = text[:left]
        out.append((st, piece))
        left -= len(piece)
        last = st
    if left > 0:
        out.append((last, " " * left))
    return out


def format_row(cols: Sequence[tuple[str, int, bool]], width: int) -> str:
    """Each col is (text, total width incl. 1 separator cell, right-align)."""
    parts: list[str] = []
    for text, w, right in cols:
        inner = max(0, w - 1)
        c = clip(_clean(text), inner)
        parts.append((c.rjust(inner) if right else c.ljust(inner)) + " ")
    return "".join(parts)[:width].ljust(width)


def parse_lsof(text: str) -> list[OpenDeleted]:
    """Parses `lsof -F pcfns` output (a record completes at its `n` line)."""
    out: list[OpenDeleted] = []
    seen: set[tuple[int, str]] = set()
    pid = 0
    cmd = ""
    size = 0
    for line in text.splitlines():
        if not line:
            continue
        tag, val = line[0], line[1:]
        if tag == "p":
            pid = int(val) if val.isdigit() else 0
            cmd = ""
            size = 0
        elif tag == "c":
            cmd = val
        elif tag == "f":
            size = 0
        elif tag == "s":
            size = int(val) if val.isdigit() else 0
        elif tag == "n":
            name = val.removesuffix(" (deleted)")
            key = (pid, name)
            if size > 0 and key not in seen:
                seen.add(key)
                out.append(OpenDeleted(pid, cmd, size, name))
            size = 0
    return sorted(out, key=lambda o: -o.size)


# --------------------------------------------------------------------------- safe deletion

def _real(p: Path) -> Path:
    return Path(os.path.realpath(p))


def _inside(real: Path, allow_real: Sequence[Path]) -> bool:
    return any(real == a or a in real.parents for a in allow_real)


def _usable_allow(allow: Sequence[Path]) -> list[Path]:
    # Entries shallower than /x/y (such as "/" or "/home") are never trusted as deletion roots.
    return [r for r in (_real(a) for a in allow) if len(r.parts) >= 3]


def safe_targets(root: Path, allow: Sequence[Path]) -> list[Path]:
    """Direct children of `root` that may be deleted, or [] when `root` is not allowlisted.

    `root` must resolve (realpath) to an allowlist entry or sit inside one. Symlink children are
    returned only when their own target is also inside the allowlist; they are unlinked, never followed.
    The root itself is never returned.
    """
    allow_real = _usable_allow(allow)
    real = _real(root)
    if not allow_real or not _inside(real, allow_real) or not real.is_dir():
        return []
    out: list[Path] = []
    try:
        children = sorted(real.iterdir())
    except OSError:
        return []
    for child in children:
        if child.is_symlink() and not _inside(_real(child), allow_real):
            continue
        out.append(child)
    return out


def dir_size(path: Path) -> int:
    """Total st_size of regular files under `path`; symlinks are not followed or counted."""
    try:
        st = path.lstat()
    except OSError:
        return 0
    if not path.is_dir() or path.is_symlink():
        return 0 if path.is_symlink() else st.st_size
    total = 0
    stack = [str(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def delete_contents(root: Path, allow: Sequence[Path]) -> DeleteResult:
    """Deletes the direct children of `root` returned by safe_targets; never `root` itself."""
    freed = errors = 0
    for t in safe_targets(root, allow):
        try:
            if t.is_symlink():
                t.unlink()
            elif t.is_dir():
                before = dir_size(t)
                shutil.rmtree(t, ignore_errors=True)
                after = dir_size(t) if os.path.lexists(t) else 0
                freed += before - after
                if after:
                    errors += 1
            else:
                size = t.lstat().st_size
                t.unlink()
                freed += size
        except OSError:
            errors += 1
    return DeleteResult(freed, errors)


def old_transcripts(projects: Path, now: float, days: int = TRANSCRIPT_DAYS) -> list[Path]:
    """*.jsonl regular files older than `days` directly inside each real project dir."""
    cutoff = now - days * 86400
    out: list[Path] = []
    try:
        dirs = sorted(projects.iterdir())
    except OSError:
        return out
    for d in dirs:
        if d.is_symlink() or not d.is_dir():
            continue
        try:
            for f in sorted(d.glob("*.jsonl")):
                if f.is_symlink() or not f.is_file():
                    continue
                if f.stat().st_mtime < cutoff:
                    out.append(f)
        except OSError:
            continue
    return out


def delete_old_transcripts(projects: Path, allow: Sequence[Path], now: float,
                           days: int = TRANSCRIPT_DAYS) -> DeleteResult:
    allow_real = _usable_allow(allow)
    if not allow_real or not _inside(_real(projects), allow_real):
        return DeleteResult()
    freed = errors = 0
    for f in old_transcripts(projects, now, days):
        try:
            size = f.stat().st_size
            f.unlink()
            freed += size
        except OSError:
            errors += 1
    return DeleteResult(freed, errors)


def _dir_specs(home: Path, uid: int) -> list[tuple[str, tuple[Path, ...], str]]:
    c = home / ".claude"
    trash = home / ".local" / "share" / "Trash"
    return [
        ("claude file-history", (c / "file-history",), "rewind checkpoints"),
        ("claude shell-snapshots", (c / "shell-snapshots",), "rebuilt per session"),
        ("claude paste-cache", (c / "paste-cache",), "pasted text cache"),
        ("claude debug", (c / "debug",), "debug logs"),
        ("claude session-env", (c / "session-env",), "per-session env"),
        ("pip cache", (home / ".cache" / "pip",), "re-downloaded on demand"),
        ("go build cache", (home / ".cache" / "go-build",), "rebuilt on demand"),
        ("npm cache", (home / ".npm" / "_cacache",), "re-downloaded on demand"),
        ("bun cache", (home / ".bun" / "install" / "cache",), "re-downloaded on demand"),
        ("cargo registry cache", (home / ".cargo" / "registry" / "cache",), "re-downloaded on demand"),
        ("Trash", (trash / "files", trash / "info"), "not recoverable afterwards"),
        ("claude tmp", (Path(f"/tmp/claude-{uid}"),), "close running sessions first"),
    ]


def allowlist(home: Path, uid: int) -> list[Path]:
    return [p for _, roots, _ in _dir_specs(home, uid) for p in roots]


def projects_root(home: Path) -> Path:
    return home / ".claude" / "projects"


def candidate_defs(home: Path, uid: int) -> list[Candidate]:
    """Cleanup rows for allowlisted paths that exist. All start unselected."""
    out: list[Candidate] = []
    for label, roots, note in _dir_specs(home, uid):
        existing = tuple(r for r in roots if r.is_dir())
        if not existing:
            continue
        shown = shorten_home(existing[0].parent if len(roots) > 1 else existing[0], home)
        out.append(Candidate(label, existing, shown, note))
    proj = projects_root(home)
    if proj.is_dir():
        out.append(Candidate(f"old Claude transcripts (>{TRANSCRIPT_DAYS} days)", (proj,),
                             shorten_home(proj, home), TRANSCRIPT_NOTE, "transcripts", danger=True))
    return out


def candidate_size(c: Candidate, now: float) -> int:
    if c.kind == "transcripts":
        total = 0
        for f in old_transcripts(c.roots[0], now):
            try:
                total += f.stat().st_size
            except OSError:
                continue
        return total
    return sum(dir_size(r) for r in c.roots)


def delete_candidate(c: Candidate, home: Path, uid: int, now: float) -> DeleteResult:
    if c.kind == "transcripts":
        proj = projects_root(home)
        return delete_old_transcripts(c.roots[0], [proj], now)
    allow = allowlist(home, uid)
    freed = errors = 0
    for r in c.roots:
        res = delete_contents(r, allow)
        freed += res.freed
        errors += res.errors
    return DeleteResult(freed, errors)


# --------------------------------------------------------------------------- collectors

def collect_procs() -> list[ProcRow]:
    # process_iter reuses Process instances between calls, so cpu_percent(None) deltas are meaningful.
    attrs = ["pid", "username", "cpu_percent", "memory_percent", "memory_info", "name", "cmdline"]
    rows: list[ProcRow] = []
    for p in psutil.process_iter(attrs=attrs):
        info = cast("Mapping[str, object]", p.info)
        mi = info.get("memory_info")
        rss = int(getattr(mi, "rss", 0) or 0)
        name = _clean(_as_str(info.get("name"), "?"))
        cl = info.get("cmdline")
        cmd = _clean(" ".join(str(x) for x in cl)) if isinstance(cl, list) and cl else name
        pid = info.get("pid")
        rows.append(ProcRow(
            pid=pid if isinstance(pid, int) else 0,
            user=_as_str(info.get("username"), "?"),
            cpu=_as_float(info.get("cpu_percent")),
            mem=_as_float(info.get("memory_percent")),
            rss=rss, name=name, cmd=cmd,
        ))
    return rows


def collect_net(names: dict[int, str]) -> list[NetRow]:
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return []
    live = {c.pid for c in conns if c.pid}
    for stale in [p for p in names if p not in live]:
        del names[stale]
    rows: list[NetRow] = []
    for c in conns:
        proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
        if c.family == socket.AF_INET6:
            proto += "6"
        pname = ""
        if c.pid:
            if c.pid not in names:
                try:
                    names[c.pid] = psutil.Process(c.pid).name()
                except psutil.Error:
                    names[c.pid] = "?"
            pname = names[c.pid]
        rows.append(NetRow(proto, fmt_addr(cast("Sequence[object]", c.laddr)),
                           fmt_addr(cast("Sequence[object]", c.raddr)) if c.raddr else "*",
                           c.status if c.status != "NONE" else "", c.pid or None, pname))
    return rows


def collect_mounts() -> list[Mount]:
    out: list[Mount] = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        if part.device in seen or part.fstype == "squashfs" or part.mountpoint.startswith("/snap"):
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        seen.add(part.device)
        out.append(Mount(part.mountpoint, part.fstype, u.total, u.used, u.free, float(u.percent)))
    return out


def collect_open_deleted() -> Optional[list[OpenDeleted]]:
    """None when lsof is missing or too slow."""
    try:
        res = subprocess.run(["lsof", "+L1", "-F", "pcfns"], capture_output=True, text=True,
                             timeout=3, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return parse_lsof(res.stdout)


# --------------------------------------------------------------------------- state + rendering

class State:
    def __init__(self, home: Optional[Path] = None, uid: Optional[int] = None) -> None:
        self.home = home if home is not None else Path.home()
        self.uid = uid if uid is not None else os.getuid()
        self.view = 0
        self.app: Optional[Application[None]] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.confirm: Optional[Confirm] = None
        self.status: Optional[tuple[str, str, float]] = None
        self.hits: list[Optional[Hit]] = []
        self.tops = [0, 0, 0]
        # processes
        self.procs: Optional[list[ProcRow]] = None
        self.sort_key: SortKey = "cpu"
        self.sel_pid: Optional[int] = None
        # network
        self.net_rows: Optional[list[NetRow]] = None
        self.listen_only = False
        self.net_cursor = 0
        self.rate_down = 0.0
        self.rate_up = 0.0
        self._net_prev: Optional[tuple[float, int, int]] = None
        self._pnames: dict[int, str] = {}
        # disk
        self.mounts: Optional[list[Mount]] = None
        self.cands: Optional[list[Candidate]] = None
        self.disk_cursor = 0
        self.disk_extra = 0
        self.open_deleted: Optional[list[OpenDeleted]] = None
        self._lsof_at = 0.0
        self._scan_gen = 0
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=3)

    # ---- plumbing
    def _poke(self) -> None:
        if self.loop is not None and self.app is not None:
            try:
                self.loop.call_soon_threadsafe(self.app.invalidate)
            except RuntimeError:
                pass

    def say(self, text: str, cls: str = "ok") -> None:
        self.status = (text, cls, time.monotonic() + 8.0)

    def set_view(self, v: int) -> None:
        self.view = v % 3
        self.kick()
        self._invalidate()

    def _invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    def kick(self) -> None:
        if self.app is not None:
            self.app.create_background_task(self._refresh_once())

    async def _refresh_once(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self.refresh)
        self._invalidate()

    async def refresh_loop(self) -> None:
        await asyncio.sleep(0.8)
        while True:
            await self._refresh_once()
            await asyncio.sleep(REFRESH_SECONDS)

    # ---- refresh
    def _sample_net(self) -> None:
        io = psutil.net_io_counters()
        now = time.monotonic()
        prev = self._net_prev
        if prev is not None:
            dt = now - prev[0]
            if dt < 0.3:
                return
            self.rate_down = max(0.0, (io.bytes_recv - prev[1]) / dt)
            self.rate_up = max(0.0, (io.bytes_sent - prev[2]) / dt)
        self._net_prev = (now, io.bytes_recv, io.bytes_sent)

    def refresh(self) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._sample_net()
            if self.view == 0:
                self.procs = collect_procs()
            elif self.view == 1:
                self.net_rows = collect_net(self._pnames)
            else:
                self.mounts = collect_mounts()
                if self.cands is None:
                    self.cands = candidate_defs(self.home, self.uid)
                    self.start_scan()
                if time.monotonic() - self._lsof_at > LSOF_EVERY:
                    self._lsof_at = time.monotonic()
                    self.open_deleted = collect_open_deleted()
        except (psutil.Error, OSError) as exc:
            self.say(f"refresh failed: {exc}", "err")
        finally:
            self._lock.release()

    def start_scan(self) -> None:
        if self.cands is None:
            return
        self._scan_gen += 1
        gen = self._scan_gen
        now = time.time()
        for c in self.cands:
            c.size = None
            self._pool.submit(self._scan_one, c, gen, now)

    def _scan_one(self, c: Candidate, gen: int, now: float) -> None:
        size = candidate_size(c, now)
        if gen == self._scan_gen:
            c.size = size
            self._poke()

    # ---- actions
    def sorted_procs(self) -> list[ProcRow]:
        return sort_procs(self.procs or [], self.sort_key)

    def visible_net(self) -> list[NetRow]:
        rows = sort_net(self.net_rows or [])
        return [r for r in rows if is_listening(r)] if self.listen_only else rows

    def _proc_index(self, rows: Sequence[ProcRow]) -> int:
        return next((i for i, r in enumerate(rows) if r.pid == self.sel_pid), 0)

    def move(self, delta: int) -> None:
        if self.view == 0:
            rows = self.sorted_procs()
            if rows:
                self.sel_pid = rows[clamp(self._proc_index(rows) + delta, 0, len(rows) - 1)].pid
        elif self.view == 1:
            self.net_cursor = clamp(self.net_cursor + delta, 0, max(0, len(self.visible_net()) - 1))
        else:
            n = len(self.cands or [])
            for _ in range(min(abs(delta), 200)):
                if delta > 0:
                    if self.disk_cursor < n - 1:
                        self.disk_cursor += 1
                    else:
                        self.disk_extra += 1
                elif self.disk_extra > 0:
                    self.disk_extra -= 1
                else:
                    self.disk_cursor = max(0, self.disk_cursor - 1)
        self._invalidate()

    def sort_by(self, key: SortKey) -> None:
        self.sort_key = key
        self._invalidate()

    def select_pid(self, pid: int) -> None:
        self.sel_pid = pid
        self._invalidate()

    def select_net(self, i: int) -> None:
        self.net_cursor = i
        self._invalidate()

    def toggle_net_listen(self) -> None:
        self.listen_only = not self.listen_only
        self.net_cursor = 0
        self._invalidate()

    def toggle_cand(self, i: int) -> None:
        if self.cands and 0 <= i < len(self.cands):
            self.disk_cursor = i
            c = self.cands[i]
            c.selected = not c.selected
        self._invalidate()

    def ask_kill(self) -> None:
        rows = self.sorted_procs()
        if not rows:
            return
        r = rows[self._proc_index(rows)]
        self.confirm = Confirm(f"SIGTERM {r.pid} {clip(r.name, 30)}? y/n", lambda: self._kill(r.pid, r.name))
        self._invalidate()

    def _kill(self, pid: int, name: str) -> None:
        if pid == os.getpid():
            self.say("refusing to terminate the panel itself", "warn")
            return
        try:
            psutil.Process(pid).terminate()
            self.say(f"sent SIGTERM to {pid} {name}")
        except psutil.NoSuchProcess:
            self.say(f"{pid} already gone", "warn")
        except psutil.AccessDenied:
            self.say(f"permission denied for {pid} {name}", "err")
        except psutil.Error as exc:
            self.say(f"kill failed: {exc}", "err")
        self.kick()

    def ask_delete(self) -> None:
        sel = [c for c in (self.cands or []) if c.selected]
        if not sel:
            self.say("nothing selected - space toggles a row", "warn")
            return
        known = sum(c.size or 0 for c in sel)
        unknown = any(c.size is None for c in sel)
        total = fmt_size(known) + ("+" if unknown else "")
        extra = " INCLUDING TRANSCRIPTS" if any(c.danger for c in sel) else ""
        self.confirm = Confirm(f"delete {len(sel)} items ({total})? y/n{extra}", lambda: self._start_delete(sel))
        self._invalidate()

    def _start_delete(self, sel: list[Candidate]) -> None:
        self.say("deleting...", "warn")
        threading.Thread(target=self._delete_worker, args=(sel,), daemon=True).start()

    def _delete_worker(self, sel: list[Candidate]) -> None:
        now = time.time()
        freed = errors = 0
        for c in sel:
            res = delete_candidate(c, self.home, self.uid, now)
            freed += res.freed
            errors += res.errors
            c.selected = False
        self.say(f"reclaimed {fmt_size(freed)}" + (f" ({errors} errors)" if errors else ""),
                 "err" if errors else "ok")
        self.start_scan()
        self._poke()

    def answer(self, yes: bool) -> None:
        conf, self.confirm = self.confirm, None
        if conf is not None and yes:
            conf.action()
        self._invalidate()

    # ---- mouse
    def on_mouse(self, ev: MouseEvent) -> None:
        t = ev.event_type
        if self.confirm is not None:
            if t == MouseEventType.MOUSE_UP:
                self.answer(False)
            return
        if t == MouseEventType.SCROLL_UP:
            self.move(-3)
        elif t == MouseEventType.SCROLL_DOWN:
            self.move(3)
        elif t == MouseEventType.MOUSE_UP:
            y = ev.position.y
            if 0 <= y < len(self.hits):
                h = self.hits[y]
                if h is not None:
                    h(ev.position.x)
                    self._invalidate()

    # ---- page builders (pure given state and width)
    def _loading(self, what: str) -> Row:
        return ([("class:muted", f" loading {what}...")], None)

    def build_procs(self, width: int) -> Page:
        show_user = width >= 62
        spec: list[tuple[str, int, bool, Optional[SortKey]]] = [("PID", 8, True, "pid")]
        if show_user:
            spec.append(("USER", 11, False, None))
        spec += [("CPU%", 7, True, "cpu"), ("MEM%", 7, True, "mem"), ("RSS", 9, True, "mem")]
        used = sum(w for _, w, _, _ in spec)
        spec.append(("COMMAND", max(1, width - used), False, "name"))
        spans: list[tuple[int, int, Optional[SortKey]]] = []
        x = 0
        hdr: Fragments = []
        for title, w, right, key in spec:
            cell = format_row([(title, w, right)], w)
            cls = "class:accent" if key == self.sort_key and key is not None else "class:muted"
            hdr.append((cls, cell))
            spans.append((x, x + w, key))
            x += w

        def header_click(px: int) -> None:
            for lo, hi, key in spans:
                if lo <= px < hi and key is not None:
                    self.sort_by(key)

        page = Page(header=[(hdr, header_click)])
        rows = self.sorted_procs()
        if self.procs is None:
            page.body.append(self._loading("processes"))
            return page
        cur = self._proc_index(rows)
        for i, r in enumerate(rows):
            vals: list[tuple[str, int, bool]] = [(str(r.pid), spec[0][1], True)]
            if show_user:
                vals.append((r.user, 11, False))
            vals += [(f"{r.cpu:.1f}", 7, True), (f"{r.mem:.1f}", 7, True), (fmt_size(r.rss), 9, True),
                     (r.cmd, spec[-1][1], False)]
            st = "class:sel" if i == cur else ("class:zebra" if i % 2 else "")
            page.body.append(([(st, format_row(vals, width))], self._pid_hit(r.pid)))
        page.focus = cur
        return page

    def _pid_hit(self, pid: int) -> Hit:
        def hit(_x: int) -> None:
            self.select_pid(pid)
        return hit

    def _net_hit(self, i: int) -> Hit:
        def hit(_x: int) -> None:
            self.select_net(i)
        return hit

    def _cand_hit(self, i: int) -> Hit:
        def hit(_x: int) -> None:
            self.toggle_cand(i)
        return hit

    def build_net(self, width: int) -> Page:
        mode = "listening only" if self.listen_only else "all"
        summary: Fragments = [("class:run", f" down {fmt_rate(self.rate_down)}"),
                              ("class:ok", f"  up {fmt_rate(self.rate_up)}"),
                              ("class:muted", f"   [{mode}]")]
        proc_w = 15 if width >= 80 else 0
        fixed = 6 + 13 + 8 + proc_w
        rem = max(2, width - fixed)
        loc_w = rem * 45 // 100
        rem_w = rem - loc_w
        widths = (6, loc_w, rem_w, 13, 8)
        titles = ("PROTO", "LOCAL", "REMOTE", "STATE", "PID")
        cols_h = [(t, w, False) for t, w in zip(titles, widths)]
        if proc_w:
            cols_h.append(("PROCESS", proc_w, False))
        page = Page(header=[(summary, None), ([("class:muted", format_row(cols_h, width))], None)])
        if self.net_rows is None:
            page.body.append(self._loading("connections"))
            return page
        rows = self.visible_net()
        cur = clamp(self.net_cursor, 0, max(0, len(rows) - 1))
        for i, r in enumerate(rows):
            vals = [(r.proto, widths[0], False), (r.local, widths[1], False), (r.remote, widths[2], False),
                    (r.status, widths[3], False), (str(r.pid) if r.pid else "-", widths[4], False)]
            if proc_w:
                vals.append((r.pname, proc_w, False))
            st = "class:sel" if i == cur else ("class:zebra" if i % 2 else "")
            page.body.append(([(st, format_row(vals, width))], self._net_hit(i)))
        if not rows:
            page.body.append(([("class:muted", " no connections")], None))
        if any(r.pid is None for r in (self.net_rows or [])) and os.geteuid() != 0:
            page.body.append(([("class:dim", " run as root to see all owners")], None))
        page.focus = cur if rows else None
        return page

    def build_disk(self, width: int) -> Page:
        page = Page()
        body = page.body
        body.append(([("class:title", " Mounts")], None))
        if self.mounts is None:
            body.append(self._loading("mounts"))
        for m in self.mounts or []:
            lw = clamp(width // 5, 6, 18)
            suffix = f" {m.percent:3.0f}% {fmt_size(m.used)}/{fmt_size(m.total)} free {fmt_size(m.free)}"
            bw = max(0, width - lw - len(suffix) - 3)
            filled, empty = usage_bar(m.percent, bw)
            body.append(([("", " " + clip(m.mount, lw).ljust(lw) + " "), (f"class:{usage_class(m.percent)}", filled),
                          ("class:muted", empty), ("", suffix)], None))
        body.append(([("", "")], None))
        body.append(([("class:title", " Cleanup candidates")], None))
        start = len(body)
        cands = self.cands
        if cands is None:
            body.append(self._loading("candidates"))
        elif not cands:
            body.append(([("class:muted", " nothing to clean")], None))
        else:
            wide = width >= 80
            lw = 24 if wide else 16
            sw = 8
            cw = 4
            flex = max(0, width - cw - lw - sw)
            pw = flex * 45 // 100 if wide else 0
            nw = flex - pw
            cur = clamp(self.disk_cursor, 0, len(cands) - 1)
            for i, c in enumerate(cands):
                vals: list[tuple[str, int, bool]] = [("[x]" if c.selected else "[ ]", cw, False),
                                                      (c.label, lw, False)]
                if pw:
                    vals.append((c.shown, pw, False))
                size = "..." if c.size is None else fmt_size(c.size)
                vals.append((size, sw, True))
                vals.append((c.note, max(1, nw), False))
                st = "class:sel" if i == cur else ("class:zebra" if i % 2 else ("class:warn" if c.danger else ""))
                body.append(([(st, format_row(vals, width))], self._cand_hit(i)))
            page.focus = 0 if cur == 0 else start + cur
        if self.open_deleted is not None:
            body.append(([("", "")], None))
            held = sum(o.size for o in self.open_deleted)
            body.append(([("class:title", " Deleted but still open"), ("class:muted", f"  {fmt_size(held)} held")], None))
            if not self.open_deleted:
                body.append(([("class:muted", " nothing held by deleted files")], None))
            for o in self.open_deleted[:50]:
                txt = f" {fmt_size(o.size):>8} {o.command}({o.pid}) {o.name}"
                body.append(([("class:muted", clip(_clean(txt), width))], None))
            body.append(([("class:dim", " restart the process to release it")], None))
        return page

    def build_page(self, width: int) -> Page:
        return (self.build_procs, self.build_net, self.build_disk)[self.view](width)

    # ---- prompt_toolkit renderers
    def size(self) -> tuple[int, int]:
        try:
            s = get_app().output.get_size()
            return max(20, s.columns), max(3, s.rows)
        except Exception:  # output without a size (detached/dumb terminal): fall back to 80x24
            return 80, 24

    def layout_rows(self, width: int, height: int) -> list[Row]:
        """Header rows, then the scrolled body window, padded to `height` rows."""
        page = self.build_page(width)
        header = page.header[:height]
        avail = max(0, height - len(header))
        n = len(page.body)
        top = self.tops[self.view]
        if page.focus is not None:
            if page.focus < top:
                top = page.focus
            elif page.focus >= top + avail:
                top = page.focus - avail + 1
        if self.view == 2:
            top = top + self.disk_extra
            self.disk_extra = min(self.disk_extra, max(0, n - avail))
            top = min(top, max(0, n - avail) if page.focus is None else top)
        top = clamp(top, 0, max(0, n - avail))
        if self.view == 2:
            self.disk_extra = clamp(self.disk_extra, 0, max(0, n - avail - (top - self.disk_extra)))
            self.tops[2] = top - self.disk_extra
        else:
            self.tops[self.view] = top
        rows = header + page.body[top: top + avail]
        while len(rows) < height:
            rows.append(([("", "")], None))
        return rows

    def render_body(self) -> Fragments:
        width, rows = self.size()
        height = max(1, rows - 2)
        laid = self.layout_rows(width, height)
        self.hits = [h for _, h in laid]
        out: Fragments = []
        for i, (frags, _h) in enumerate(laid):
            out += fit_frags(frags, width)
            if i < len(laid) - 1:
                out.append(("", "\n"))
        return out

    def render_top(self) -> MFragments:
        out: MFragments = [("class:title", " system "), ("class:bar", " ")]
        for i, name in enumerate(VIEW_NAMES):
            def go(ev: MouseEvent, idx: int = i) -> None:
                if ev.event_type == MouseEventType.MOUSE_UP:
                    self.set_view(idx)
            out.append(("class:btn.on" if i == self.view else "class:btn", f" {i + 1} {name} ", go))
            out.append(("class:bar", " "))
        return out

    HINTS = (
        " j/down move  c cpu  m mem  k kill  r refresh  1-3/Tab view  q quit",
        " j/k move  l listening-only  r refresh  1-3/Tab view  q quit",
        " j/k move  space toggle  x delete selected  R rescan  r refresh  1-3/Tab view  q quit",
    )

    def render_bottom(self) -> Fragments:
        width, _ = self.size()
        if self.confirm is not None:
            return fit_frags([("class:btn.danger", " " + self.confirm.prompt)], width)
        frags: Fragments = []
        used = 0
        if self.status is not None and self.status[2] > time.monotonic():
            msg = f" {clip(self.status[0], max(1, width // 2))} "
            frags.append((f"class:{self.status[1]}", msg))
            used = len(msg)
        frags.append(("class:bar", clip(self.HINTS[self.view], max(0, width - used))))
        return fit_frags(frags, width)


class BodyControl(FormattedTextControl):
    def __init__(self, state: State) -> None:
        super().__init__(text=state.render_body, focusable=False, show_cursor=False)
        self._state = state

    def mouse_handler(self, mouse_event: MouseEvent) -> None:  # type: ignore[override]
        self._state.on_mouse(mouse_event)


def build_app(state: State) -> Application[None]:
    kb = KeyBindings()
    confirming = Condition(lambda: state.confirm is not None)
    normal = ~confirming
    in_procs = Condition(lambda: state.view == 0)
    in_net = Condition(lambda: state.view == 1)
    in_disk = Condition(lambda: state.view == 2)
    not_procs = ~in_procs

    def quit_app(event: KeyPressEvent) -> None:
        event.app.exit()

    kb.add("c-c")(quit_app)
    kb.add("q", filter=normal)(quit_app)

    @kb.add("y", filter=confirming)
    def _yes(event: KeyPressEvent) -> None:
        state.answer(True)

    @kb.add("n", filter=confirming)
    @kb.add("escape", filter=confirming, eager=True)
    @kb.add("q", filter=confirming)
    @kb.add("enter", filter=confirming)
    def _no(event: KeyPressEvent) -> None:
        state.answer(False)

    for i in range(3):
        kb.add(str(i + 1), filter=normal)(lambda event, i=i: state.set_view(i))
    kb.add("tab", filter=normal)(lambda event: state.set_view(state.view + 1))
    kb.add("s-tab", filter=normal)(lambda event: state.set_view(state.view - 1))
    kb.add("up", filter=normal)(lambda event: state.move(-1))
    kb.add("down", filter=normal)(lambda event: state.move(1))
    kb.add("j", filter=normal)(lambda event: state.move(1))
    kb.add("k", filter=normal & not_procs)(lambda event: state.move(-1))
    kb.add("pageup", filter=normal)(lambda event: state.move(-10))
    kb.add("pagedown", filter=normal)(lambda event: state.move(10))
    kb.add("home", filter=normal)(lambda event: state.move(-10**6))
    kb.add("end", filter=normal)(lambda event: state.move(10**6))
    kb.add("r", filter=normal)(lambda event: state.kick())
    kb.add("c", filter=normal & in_procs)(lambda event: state.sort_by("cpu"))
    kb.add("m", filter=normal & in_procs)(lambda event: state.sort_by("mem"))
    kb.add("k", filter=normal & in_procs)(lambda event: state.ask_kill())
    kb.add("l", filter=normal & in_net)(lambda event: state.toggle_net_listen())
    kb.add("space", filter=normal & in_disk)(lambda event: state.toggle_cand(state.disk_cursor))
    kb.add("x", filter=normal & in_disk)(lambda event: state.ask_delete())
    kb.add("R", filter=normal & in_disk)(lambda event: state.start_scan())

    top = Window(FormattedTextControl(state.render_top), height=1, style="class:bar")
    body = Window(BodyControl(state), wrap_lines=False)
    bottom = Window(FormattedTextControl(state.render_bottom), height=1, style="class:bar")
    app: Application[None] = Application(
        layout=Layout(HSplit([top, body, bottom])), key_bindings=kb, style=style(),
        full_screen=True, mouse_support=True,
    )
    state.app = app
    return app


def main() -> int:
    state = State()
    state.refresh()  # primes cpu_percent so the first visible refresh has real deltas
    app = build_app(state)

    def pre_run() -> None:
        state.loop = asyncio.get_running_loop()
        app.create_background_task(state.refresh_loop())

    app.run(pre_run=pre_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
