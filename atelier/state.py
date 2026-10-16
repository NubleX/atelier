"""What the harness remembers between runs: open tabs, their names, folders and Claude sessions."""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

CONFIG_DIR = Path(os.environ.get("ATELIER_CONFIG_DIR", str(Path.home() / ".config" / "atelier")))
STATE_FILE = CONFIG_DIR / "state.json"
LOCK_FILE = CONFIG_DIR / "state.lock"
PROJECTS_ROOT = Path.home() / ".claude" / "projects"


@dataclass
class Tab:
    name: str
    cwd: str
    session: str = ""


@dataclass
class State:
    tabs: list[Tab] = field(default_factory=list)
    selected: int = -1  # position in `tabs` of the tab that was in front; -1 = home


def encode_project_dir(cwd: str) -> str:
    """Claude Code names a project's transcript folder after its path, every non-alphanumeric becoming '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def transcript_dir(cwd: str) -> Path:
    return PROJECTS_ROOT / encode_project_dir(cwd)


def newest_session(cwd: str, since: float, taken: set[str]) -> str:
    """Id of the newest transcript in cwd's project folder modified after `since`, skipping ids other tabs hold."""
    d = transcript_dir(cwd)
    best, best_m = "", since
    try:
        for p in d.glob("*.jsonl"):
            if p.stem in taken:
                continue
            m = p.stat().st_mtime
            if m > best_m:
                best, best_m = p.stem, m
    except OSError:
        return ""
    return best


def load() -> State:
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        tabs = [Tab(str(t["name"]), str(t["cwd"]), str(t.get("session", ""))) for t in raw.get("tabs", []) if t.get("cwd")]
        return State(tabs, int(raw.get("selected", -1)))
    except (OSError, ValueError, KeyError, TypeError):
        return State()


def save(state: State) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tabs": [asdict(t) for t in state.tabs], "selected": state.selected}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_FILE)


class Locked(Exception):
    pass


@contextmanager
def locked() -> Iterator[None]:
    """Context-manager style guard: raises Locked at once when another save is running."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise Locked()
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def last_active(cwd: str) -> float:
    """Most recent sign of life in a project: its newest Claude transcript, else the folder itself."""
    d = transcript_dir(cwd)
    try:
        newest = max((p.stat().st_mtime for p in d.glob("*.jsonl")), default=0.0)
    except OSError:
        newest = 0.0
    try:
        return max(newest, Path(cwd).stat().st_mtime)
    except OSError:
        return newest


def now() -> int:
    return int(time.time())
