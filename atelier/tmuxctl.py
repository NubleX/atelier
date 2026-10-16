"""Thin wrapper over the dedicated tmux server (socket `atelier`) that hosts the harness."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional

SOCKET = os.environ.get("ATELIER_SOCKET", "atelier")
SESSION = "atelier"


class TmuxError(RuntimeError):
    pass


def run(*args: str) -> tuple[int, str]:
    r = subprocess.run(["tmux", "-L", SOCKET, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.rstrip("\n") if r.returncode == 0 else r.stderr.strip()


def tmux(*args: str) -> str:
    rc, out = run(*args)
    if rc != 0:
        raise TmuxError(f"tmux {' '.join(args)}: {out}")
    return out


def server_alive() -> bool:
    return run("has-session", "-t", SESSION)[0] == 0


def get_option(target: str, name: str, window: bool = True) -> str:
    rc, out = run("show-options", "-wqv" if window else "-pqv", "-t", target, name)
    return out if rc == 0 else ""


def set_option(target: str, name: str, value: str, window: bool = True) -> None:
    run("set-option", "-w" if window else "-p", "-t", target, name, value)


@dataclass(frozen=True)
class Win:
    id: str
    index: int
    name: str
    kind: str
    cwd: str
    session: str
    created: int
    active: bool
    width: int


_WIN_FMT = "\t".join(["#{window_id}", "#{window_index}", "#{window_name}", "#{@kind}", "#{@cwd}", "#{@session}",
                      "#{@created}", "#{window_active}", "#{window_width}"])


def list_windows() -> list[Win]:
    rc, out = run("list-windows", "-t", SESSION, "-F", _WIN_FMT)
    wins: list[Win] = []
    if rc != 0:
        return wins
    for line in out.splitlines():
        f = line.split("\t")
        if len(f) < 9:
            continue
        wins.append(Win(f[0], int(f[1]), f[2], f[3], f[4], f[5], int(f[6] or 0), f[7] == "1", int(f[8] or 0)))
    return wins


@dataclass(frozen=True)
class Pane:
    id: str
    role: str
    width: int


def list_panes(window_id: str) -> list[Pane]:
    rc, out = run("list-panes", "-t", window_id, "-F", "#{pane_id}\t#{@role}\t#{pane_width}")
    panes: list[Pane] = []
    if rc != 0:
        return panes
    for line in out.splitlines():
        f = line.split("\t")
        if len(f) == 3:
            panes.append(Pane(f[0], f[1], int(f[2] or 0)))
    return panes


def claude_pane(window_id: str) -> Optional[str]:
    for p in list_panes(window_id):
        if p.role == "claude":
            return p.id
    return None


def send_text(pane_id: str, text: str, enter: bool = False) -> None:
    run("send-keys", "-t", pane_id, "-l", text)
    if enter:
        run("send-keys", "-t", pane_id, "Enter")
