"""atelier: one Aurelia-skinned harness around real Claude Code, hosted in tmux.

Commands (run by you, by the tmux keys and by the panels):
  atelier                 start or re-attach; restores the tabs from last time
  atelier new DIR [NAME]  open a tab for DIR
  atelier panel NAME      run a panel: welcome tree dash system pick
  atelier layout          reflow the side panels to the terminal width
  atelier toggle ROLE     show or hide the tree or dash panel in this tab
  atelier snap WINDOW_ID  after a border drag: snap the panel widths to presets, hide a panel dragged shut
  atelier reset           default panel widths, panels visible again
  atelier system          show the system tab
  atelier save | tick | close WINDOW_ID
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from atelier import state as st
from atelier.tmuxctl import (SESSION, SOCKET, TmuxError, Win, claude_pane, get_option, list_panes, list_windows, run,
                             server_alive, set_option, tmux)
from atelier.tmuxconf import render

ROOT = Path(__file__).resolve().parent.parent
BIN = str(ROOT / "bin" / "atelier")
CONF = st.CONFIG_DIR / "tmux.conf"
DEFAULT_WIDTHS = {"tree": 30, "dash": 44}
PRESETS = {"tree": (20, 30, 40, 50), "dash": (32, 44, 60, 80)}  # widths a dragged border snaps to
SNAP_RANGE = 3  # columns from a preset within which a dragged border is pulled onto it
COLLAPSE_BELOW = 14  # a panel dragged narrower than this closes
MIN_CLAUDE = 60  # columns Claude always keeps
TREE_MIN, DASH_MIN = 100, 150  # window widths from which each side panel is shown
SIDE_ROLES = ("tree", "dash")


def snap_width(role: str, width: int) -> int:
    """Pulls a dragged width onto the nearest preset when it is within SNAP_RANGE, else leaves it."""
    nearest = min(PRESETS[role], key=lambda p: abs(p - width))
    return nearest if abs(nearest - width) <= SNAP_RANGE else width


def side_width(role: str, window_width: int, panels: int) -> int:
    """The remembered (else default) width of a side panel, capped so Claude keeps MIN_CLAUDE columns."""
    wanted = st.load_widths().get(role, DEFAULT_WIDTHS[role])
    cap = max(COLLAPSE_BELOW, (window_width - MIN_CLAUDE - 2) // max(panels, 1))
    return min(wanted, cap)


def write_conf() -> None:
    st.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONF.write_text(render(BIN), encoding="utf-8")


def claude_command(cwd: str, session: str) -> str:
    resume = f" --resume {session}" if session and (st.transcript_dir(cwd) / f"{session}.jsonl").is_file() else ""
    # fullscreen is what makes Claude Code report mouse clicks, so its mods' buttons work; ATELIER_FULLSCREEN=0 opts out
    fullscreen = "" if os.environ.get("ATELIER_FULLSCREEN") == "0" else "CLAUDE_CODE_NO_FLICKER=1 "
    return f"exec env -u CLAUDE_CODE_DISABLE_MOUSE {fullscreen}COLORTERM=truecolor claude{resume}"


def new_work_tab(cwd: str, name: str = "", session: str = "", select: bool = True) -> str:
    folder = cwd if Path(cwd).is_dir() else str(Path.home())
    wid = tmux("new-window", "-d", "-P", "-F", "#{window_id}", "-n", name or Path(folder).name or "home",
               "-c", folder, claude_command(folder, session))
    set_option(wid, "@kind", "work")
    set_option(wid, "@cwd", folder)
    set_option(wid, "@session", session)
    set_option(wid, "@created", str(st.now()))
    for pane in list_panes(wid):
        set_option(pane.id, "@role", "claude", window=False)
    layout_window(wid)
    if select:
        tmux("select-window", "-t", wid)
    return wid


def ensure_system(select: bool = True) -> None:
    for w in list_windows():
        if w.kind == "system":
            if select:
                tmux("select-window", "-t", w.id)
            return
    wid = tmux("new-window", "-d", "-P", "-F", "#{window_id}", "-n", "system", "-c", str(Path.home()), f"{BIN} panel system")
    set_option(wid, "@kind", "system")
    if select:
        tmux("select-window", "-t", wid)


def wanted_roles(win: Win) -> list[str]:
    hidden = set(filter(None, get_option(win.id, "@hide").split(",")))
    out = []
    if win.width >= TREE_MIN and "tree" not in hidden:
        out.append("tree")
    if win.width >= DASH_MIN and "dash" not in hidden:
        out.append("dash")
    return out


def layout_window(window_id: str) -> None:
    """Idempotent: adds the side panels the width allows, removes the rest, restores their widths."""
    win = next((w for w in list_windows() if w.id == window_id), None)
    if win is None or win.kind != "work":
        return
    panes = list_panes(window_id)
    main = next((p for p in panes if p.role == "claude"), None)
    if main is None:
        main = next((p for p in panes if p.role == ""), None)
        if main is None:
            return
        set_option(main.id, "@role", "claude", window=False)
    want = wanted_roles(win)
    have = {p.role: p for p in panes if p.role in SIDE_ROLES}
    for role, pane in have.items():
        if role not in want:
            run("kill-pane", "-t", pane.id)
    for role in want:
        size = side_width(role, win.width, len(want))
        if role in have:
            if have[role].width != size:
                run("resize-pane", "-t", have[role].id, "-x", str(size))
            continue
        flags = ["-hb"] if role == "tree" else ["-h"]
        rc, pane_id = run("split-window", *flags, "-d", "-l", str(size), "-t", main.id, "-P", "-F", "#{pane_id}",
                          "-e", f"ATELIER_WIN={window_id}", f"{BIN} panel {role}")
        if rc == 0:
            set_option(pane_id, "@role", role, window=False)


def layout_all() -> None:
    for w in list_windows():
        if w.kind == "work":
            layout_window(w.id)


def toggle(role: str) -> None:
    if role not in SIDE_ROLES:
        return
    rc, wid = run("display-message", "-p", "#{window_id}")
    if rc != 0:
        return
    hidden = set(filter(None, get_option(wid, "@hide").split(",")))
    hidden.symmetric_difference_update({role})
    set_option(wid, "@hide", ",".join(sorted(hidden)))
    layout_window(wid)


def snap_window(window_id: str) -> None:
    """Runs when a border drag ends: remembers the new widths, pulls them onto presets, closes a panel dragged shut."""
    win = next((w for w in list_windows() if w.id == window_id), None)
    if win is None or win.kind != "work":
        return
    widths = st.load_widths()
    hidden = set(filter(None, get_option(window_id, "@hide").split(",")))
    for pane in list_panes(window_id):
        if pane.role not in SIDE_ROLES:
            continue
        if pane.width < COLLAPSE_BELOW:
            hidden.add(pane.role)
        else:
            widths[pane.role] = snap_width(pane.role, pane.width)
    st.save_widths(widths)
    set_option(window_id, "@hide", ",".join(sorted(hidden)))
    layout_window(window_id)


def reset_layout() -> None:
    rc, wid = run("display-message", "-p", "#{window_id}")
    if rc != 0:
        return
    st.save_widths({})
    set_option(wid, "@hide", "")
    layout_all()


def save_state(allow_empty: bool = False) -> None:
    try:
        with st.locked():
            wins = [w for w in list_windows() if w.kind == "work"]
            if not wins and not allow_empty:
                return  # a server going down must not wipe what was remembered
            claimed = {w.session for w in wins if w.session}
            tabs: list[st.Tab] = []
            selected = -1
            for w in wins:
                session = w.session
                found = st.newest_session(w.cwd, max(float(w.created) - 120, 0.0), claimed - {session})
                if found and found != session:
                    old_m = _mtime(st.transcript_dir(w.cwd) / f"{session}.jsonl") if session else 0.0
                    if _mtime(st.transcript_dir(w.cwd) / f"{found}.jsonl") > old_m:
                        session = found
                        claimed.add(found)
                        set_option(w.id, "@session", found)
                tabs.append(st.Tab(w.name, w.cwd, session))
                if w.active:
                    selected = len(tabs) - 1
            st.save(st.State(tabs, selected))
    except st.Locked:
        return


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def close_window(window_id: str) -> None:
    run("kill-window", "-t", window_id)
    save_state(allow_empty=True)


def start_server() -> None:
    write_conf()
    tmux("-f", str(CONF), "new-session", "-d", "-s", SESSION, "-n", "home", "-x", "200", "-y", "50",
         f"{BIN} panel welcome")
    wid = tmux("display-message", "-p", "-t", f"{SESSION}:1", "#{window_id}")
    set_option(wid, "@kind", "home")
    saved = st.load()
    front: Optional[str] = None
    for i, t in enumerate(saved.tabs):
        try:
            w = new_work_tab(t.cwd, t.name, t.session, select=False)
        except TmuxError:
            continue
        if i == saved.selected:
            front = w
    if front:
        tmux("select-window", "-t", front)


def attach() -> None:
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env.setdefault("COLORTERM", "truecolor")
    os.execvpe("tmux", ["tmux", "-L", SOCKET, "attach-session", "-t", SESSION], env)


def open_harness() -> int:
    if not sys.stdout.isatty():
        print("atelier needs a terminal", file=sys.stderr)
        return 1
    if not server_alive():
        start_server()
    else:
        write_conf()
        run("source-file", str(CONF))
    attach()
    return 0


def run_panel(name: str) -> int:
    if name == "welcome":
        from atelier.panels.welcome import main as panel_main
    elif name == "pick":
        from atelier.panels.welcome import pick_main as panel_main
    elif name == "tree":
        from atelier.panels.tree import main as panel_main
    elif name == "dash":
        from atelier.panels.dash import main as panel_main
    elif name == "system":
        from atelier.panels.system import main as panel_main
    else:
        print(f"unknown panel: {name}", file=sys.stderr)
        return 2
    return panel_main()


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "open"
    rest = argv[1:]
    if cmd in ("open", "attach"):
        return open_harness()
    if cmd == "panel" and rest:
        return run_panel(rest[0])
    if cmd == "new" and rest:
        new_work_tab(str(Path(rest[0]).expanduser().resolve()), rest[1] if len(rest) > 1 else "")
        return 0
    if cmd == "layout":
        layout_all()
        return 0
    if cmd == "toggle" and rest:
        toggle(rest[0])
        return 0
    if cmd == "snap" and rest:
        snap_window(rest[0])
        return 0
    if cmd == "reset":
        reset_layout()
        return 0
    if cmd == "system":
        ensure_system()
        return 0
    if cmd in ("save", "tick"):
        save_state()
        return 0
    if cmd == "close" and rest:
        close_window(rest[0])
        return 0
    print(__doc__)
    return 0 if cmd in ("-h", "--help", "help") else 2
