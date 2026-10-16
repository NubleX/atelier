#!/usr/bin/env python3
"""ccadmin: view, disable and delete Claude Code plugins, skills and MCP servers.

Mouse and keyboard. Run `ccadmin` in any terminal, or `ccadmin --window` to open its own window.
Plugins and MCP servers go through the `claude` CLI; skills are unlinked, never erased from the library.
"""
from __future__ import annotations

import curses
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
SETTINGS = CLAUDE_DIR / "settings.json"
INSTALLED = CLAUDE_DIR / "plugins" / "installed_plugins.json"
USER_SKILLS = CLAUDE_DIR / "skills"
AGENT_SKILLS = HOME / ".agents" / "skills"
CLAUDE_JSON = HOME / ".claude.json"
STRAY_MCP = CLAUDE_DIR / "mcp.json"
TABS = ("Plugins", "Skills", "MCP")
WHEEL_UP = getattr(curses, "BUTTON4_PRESSED", 0)
WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0x200000)
RESTART_NOTE = "restart Claude Code sessions to apply"


@dataclass
class Item:
    key: str
    name: str
    detail: str
    enabled: Optional[bool]  # None: no on/off switch
    removable: bool
    scope: str = ""
    cwd: Optional[str] = None  # working dir for CLI actions (local-scope MCP)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def mask(text: str) -> str:
    """Keeps keys and tokens off the screen."""
    return re.sub(r"[A-Za-z0-9_\-]{24,}", "...", text)


def server_summary(cfg: object) -> str:
    if not isinstance(cfg, dict):
        return ""
    url = cfg.get("url")
    if isinstance(url, str):
        return mask(f"{cfg.get('type', 'http')} {url.split('?')[0]}")
    parts = [str(cfg.get("command", ""))] + [str(a) for a in cfg.get("args", []) if isinstance(cfg.get("args"), list)]
    return mask(" ".join(parts).strip())


def skill_description(md: Path) -> str:
    try:
        text = md.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""
    m = re.search(r"^description:\s*(.*?)(?=^[A-Za-z][\w-]*:|^---)", text, re.S | re.M)
    return re.sub(r"\s+", " ", m.group(1)).strip().strip("\"'>|").strip() if m else ""


def load_plugins() -> list[Item]:
    installed = read_json(INSTALLED).get("plugins", {})
    enabled = read_json(SETTINGS).get("enabledPlugins", {})
    items = []
    for pid, entries in installed.items():
        e = entries[0] if entries else {}
        items.append(Item(pid, pid, f"v{e.get('version', '?')}  {e.get('scope', 'user')}", bool(enabled.get(pid, False)), True))
    items.sort(key=lambda i: (not i.enabled, i.name))
    return items


def load_skills() -> list[Item]:
    overrides = read_json(SETTINGS).get("skillOverrides", {})
    items = []
    if not USER_SKILLS.is_dir():
        return items
    for p in sorted(USER_SKILLS.iterdir()):
        if p.name.startswith(".") or not (p / "SKILL.md").is_file():
            continue
        ov = overrides.get(p.name)
        desc = skill_description(p / "SKILL.md")
        detail = f"({ov}) {desc}" if ov and ov != "off" else desc
        items.append(Item(p.name, p.name, detail, ov != "off", True, "link" if p.is_symlink() else "folder"))
    return items


def load_mcp() -> list[Item]:
    items: list[Item] = []
    cj = read_json(CLAUDE_JSON)
    for name, cfg in (cj.get("mcpServers") or {}).items():
        items.append(Item(f"user:{name}", name, f"user    {server_summary(cfg)}", None, True, "user"))
    for proj, v in (cj.get("projects") or {}).items():
        for name, cfg in ((v or {}).get("mcpServers") or {}).items():
            items.append(Item(f"local:{proj}:{name}", name, f"local   {proj.replace(str(HOME), '~')}  {server_summary(cfg)}", None, True, "local", proj))
    installed = read_json(INSTALLED).get("plugins", {})
    enabled = read_json(SETTINGS).get("enabledPlugins", {})
    for pid, entries in installed.items():
        if not enabled.get(pid) or not entries:
            continue
        base = Path(entries[0].get("installPath", ""))
        found = read_json(base / ".mcp.json")
        servers = found.get("mcpServers", found)
        manifest = read_json(base / ".claude-plugin" / "plugin.json").get("mcpServers")
        if isinstance(manifest, dict):
            servers = {**servers, **manifest}
        for name, cfg in servers.items():
            if isinstance(cfg, dict):
                items.append(Item(f"plugin:{pid}:{name}", name, f"plugin  {pid.split('@')[0]}  {server_summary(cfg)}", None, False, "plugin"))
    for name, cfg in (read_json(STRAY_MCP).get("mcpServers") or {}).items():
        items.append(Item(f"file:{name}", name, f"file    ~/.claude/mcp.json (not a Claude Code config path, likely unused)  {server_summary(cfg)}", None, False, "file"))
    return items


def run_cli(args: list[str], cwd: Optional[str] = None) -> tuple[bool, str]:
    try:
        r = subprocess.run(["claude", *args], capture_output=True, text=True, timeout=90, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"claude {' '.join(args)}: {exc}"
    out = (r.stdout.strip() or r.stderr.strip()).splitlines()
    return r.returncode == 0, (out[-1] if out else f"exit {r.returncode}")


def write_settings(data: dict) -> None:
    shutil.copy2(SETTINGS, SETTINGS.with_name("settings.json.ccadmin-bak"))
    tmp = SETTINGS.with_name("settings.json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, SETTINGS)


def toggle_skill(item: Item) -> str:
    data = read_json(SETTINGS)
    ov = data.setdefault("skillOverrides", {})
    if ov.get(item.name) == "off":
        ov.pop(item.name)
        msg = f"enabled skill {item.name}"
    else:
        ov[item.name] = "off"
        msg = f"disabled skill {item.name}"
    if not ov:
        data.pop("skillOverrides")
    write_settings(data)
    return f"{msg}: {RESTART_NOTE}"


def delete_skill(item: Item) -> str:
    link = USER_SKILLS / item.name
    if not link.is_symlink():
        return f"refused: {item.name} is a real folder, not a link. Move it into ~/.claude/library first"
    link.unlink()
    agent_link = AGENT_SKILLS / item.name
    if agent_link.is_symlink():
        agent_link.unlink()
    return f"unlinked {item.name} (the file in the library is kept): {RESTART_NOTE}"


class App:
    def __init__(self, scr: "curses.window") -> None:
        self.scr = scr
        self.tab = 0
        self.data: list[list[Item]] = [[], [], []]
        self.cursor = [0, 0, 0]
        self.top = [0, 0, 0]
        self.filter = ""
        self.filtering = False
        self.status = ""
        self.confirm: Optional[tuple[str, Callable[[], str]]] = None
        self.buttons: list[tuple[int, int, int, str]] = []
        self.tab_spans: list[tuple[int, int]] = []
        self.reload()

    def reload(self) -> None:
        self.data = [load_plugins(), load_skills(), load_mcp()]

    def items(self) -> list[Item]:
        f = self.filter.lower()
        src = self.data[self.tab]
        return [i for i in src if f in f"{i.name} {i.detail}".lower()] if f else src

    def current(self) -> Optional[Item]:
        items = self.items()
        c = self.cursor[self.tab]
        return items[c] if 0 <= c < len(items) else None

    def put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and 0 <= x < w:
            try:
                self.scr.addnstr(y, x, text, w - x, attr)
            except curses.error:
                pass

    def draw(self) -> None:
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        if h < 10 or w < 50:
            self.put(0, 0, "window too small")
            scr.refresh()
            return
        self.put(0, 1, "ccadmin", curses.A_BOLD)
        self.put(0, 9, "Claude Code plugins, skills and MCP servers", curses.A_DIM)
        self.tab_spans = []
        x = 1
        for i, name in enumerate(TABS):
            label = f" {i + 1} {name} ({len(self.data[i])}) "
            self.put(1, x, label, curses.A_REVERSE | curses.A_BOLD if i == self.tab else 0)
            self.tab_spans.append((x, x + len(label)))
            x += len(label) + 1
        self.put(2, 0, "-" * w, curses.A_DIM)

        items = self.items()
        visible = h - 6
        c = max(0, min(self.cursor[self.tab], len(items) - 1))
        self.cursor[self.tab] = c
        top = self.top[self.tab]
        top = c if c < top else c - visible + 1 if c >= top + visible else top
        top = max(0, min(top, max(0, len(items) - visible)))
        self.top[self.tab] = top
        name_w = min(38, max(18, w // 3))
        for row in range(visible):
            idx = top + row
            if idx >= len(items):
                break
            it = items[idx]
            y = 3 + row
            sel = idx == c
            base = curses.A_REVERSE if sel else (curses.A_DIM if it.enabled is False else 0)
            if sel:
                self.put(y, 0, " " * w, curses.A_REVERSE)
            mark = " - " if it.enabled is None else "[x]" if it.enabled else "[ ]"
            self.put(y, 1, mark, base | (curses.color_pair(1) if it.enabled and not sel else 0))
            self.put(y, 5, it.name[:name_w].ljust(name_w), base | curses.A_BOLD)
            self.put(y, 5 + name_w + 2, it.detail, base)
        if not items:
            self.put(4, 3, "nothing here" + (" matches the filter" if self.filter else ""), curses.A_DIM)
        if len(items) > visible:
            self.put(2, w - 14, f" {c + 1}/{len(items)} ", curses.A_DIM)

        if self.confirm:
            self.put(h - 3, 1, self.confirm[0], curses.A_BOLD | curses.color_pair(2))
        elif self.filtering:
            self.put(h - 3, 1, f"filter: {self.filter}_", curses.A_BOLD)
        else:
            self.put(h - 3, 1, self.status or (f"filter: {self.filter}" if self.filter else ""), curses.A_BOLD)

        self.buttons = []
        if self.confirm:
            spec = [("y", " y Yes "), ("n", " n No ")]
        else:
            spec = [("toggle", " Space Toggle "), ("delete", " d Delete "), ("reload", " r Refresh "),
                    ("filter", " / Filter "), ("quit", " q Quit ")]
        x = 1
        for act, label in spec:
            self.put(h - 2, x, label, curses.A_REVERSE)
            self.buttons.append((x, x + len(label), h - 2, act))
            x += len(label) + 1
        self.put(h - 1, 1, "click, or: arrows move  tab/1-3 switch  enter toggles  d deletes  wheel scrolls", curses.A_DIM)
        scr.refresh()

    def busy(self, text: str) -> None:
        self.status = text
        self.draw()

    def toggle(self) -> None:
        it = self.current()
        if not it:
            return
        if self.tab == 0:
            verb = "disable" if it.enabled else "enable"
            self.busy(f"{verb} {it.name} ...")
            ok, msg = run_cli(["plugin", verb, it.key])
            self.status = f"{verb}d {it.name}: {RESTART_NOTE}" if ok else msg
        elif self.tab == 1:
            self.status = toggle_skill(it)
        else:
            self.status = "MCP servers have no global off switch: press d to remove, or disable the plugin that ships it"
        self.reload()

    def ask_delete(self) -> None:
        it = self.current()
        if not it:
            return
        if not it.removable:
            self.status = "read-only here: " + ("disable the plugin that ships it" if it.scope == "plugin" else "edit the file by hand")
            return
        if self.tab == 0:
            prompt = f"Uninstall plugin {it.name}?"
            fn = lambda: self._cli_delete(["plugin", "uninstall", it.key], None, it.name)  # noqa: E731
        elif self.tab == 1:
            prompt = f"Unlink skill {it.name}? (library copy stays)"
            fn = lambda: delete_skill(it)  # noqa: E731
        else:
            prompt = f"Remove MCP server {it.name} ({it.scope} scope)?"
            fn = lambda: self._cli_delete(["mcp", "remove", "-s", it.scope, it.name], it.cwd, it.name)  # noqa: E731
        self.confirm = (prompt + "   y / n", fn)

    def _cli_delete(self, args: list[str], cwd: Optional[str], name: str) -> str:
        self.busy(f"removing {name} ...")
        ok, msg = run_cli(args, cwd)
        return f"removed {name}: {RESTART_NOTE}" if ok else msg

    def do(self, action: str) -> bool:
        if self.confirm:
            fn = self.confirm[1]
            self.confirm = None
            if action == "y":
                self.status = fn()
                self.reload()
            else:
                self.status = "cancelled"
            return True
        if action == "quit":
            return False
        if action == "toggle":
            self.toggle()
        elif action == "delete":
            self.ask_delete()
        elif action == "reload":
            self.reload()
            self.status = "refreshed"
        elif action == "filter":
            self.filtering = True
        return True

    def move(self, delta: int) -> None:
        n = len(self.items())
        self.cursor[self.tab] = max(0, min(self.cursor[self.tab] + delta, max(0, n - 1)))

    def switch(self, tab: int) -> None:
        self.tab = tab % len(TABS)
        self.filter = ""

    def click(self, mx: int, my: int) -> bool:
        for x0, x1, y, act in self.buttons:
            if y == my and x0 <= mx < x1:
                return self.do(act)
        if self.confirm:
            return True
        if my == 1:
            for i, (x0, x1) in enumerate(self.tab_spans):
                if x0 <= mx < x1:
                    self.switch(i)
            return True
        idx = self.top[self.tab] + (my - 3)
        h, _ = self.scr.getmaxyx()
        if 3 <= my < h - 3 and 0 <= idx < len(self.items()):
            self.cursor[self.tab] = idx
            if 1 <= mx <= 3 and self.items()[idx].enabled is not None:
                self.toggle()
        return True

    def key(self, ch: int) -> bool:
        if ch == curses.KEY_MOUSE:
            try:
                _, mx, my, _, b = curses.getmouse()
            except curses.error:
                return True
            if b & WHEEL_UP:
                self.move(-3)
            elif b & WHEEL_DOWN:
                self.move(3)
            elif b & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                return self.click(mx, my)
            return True
        if ch == curses.KEY_RESIZE:
            return True
        if self.confirm:
            if ch in (ord("y"), ord("Y"), 10, curses.KEY_ENTER):
                return self.do("y")
            if ch in (ord("n"), ord("N"), 27):
                return self.do("n")
            return True
        if self.filtering:
            if ch in (10, curses.KEY_ENTER):
                self.filtering = False
            elif ch == 27:
                self.filtering, self.filter = False, ""
            elif ch in (127, 8, curses.KEY_BACKSPACE):
                self.filter = self.filter[:-1]
            elif 32 <= ch < 127:
                self.filter += chr(ch)
            self.cursor[self.tab] = 0
            return True
        self.status = ""
        moves = {curses.KEY_UP: -1, ord("k"): -1, curses.KEY_DOWN: 1, ord("j"): 1,
                 curses.KEY_PPAGE: -10, curses.KEY_NPAGE: 10, curses.KEY_HOME: -10**6, ord("g"): -10**6,
                 curses.KEY_END: 10**6, ord("G"): 10**6}
        if ch in moves:
            self.move(moves[ch])
        elif ch in (9, curses.KEY_RIGHT):
            self.switch(self.tab + 1)
        elif ch in (curses.KEY_BTAB, curses.KEY_LEFT):
            self.switch(self.tab - 1)
        elif ch in (ord("1"), ord("2"), ord("3")):
            self.switch(ch - ord("1"))
        elif ch in (ord(" "), 10, curses.KEY_ENTER):
            return self.do("toggle")
        elif ch in (ord("d"), curses.KEY_DC):
            return self.do("delete")
        elif ch == ord("r"):
            return self.do("reload")
        elif ch == ord("/"):
            return self.do("filter")
        elif ch == 27:
            self.filter = ""
        elif ch in (ord("q"), ord("Q")):
            return False
        return True


def run(scr: "curses.window") -> None:
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.mouseinterval(0)
    scr.keypad(True)
    app = App(scr)
    while True:
        app.draw()
        if not app.key(scr.getch()):
            return


def open_window() -> int:
    term = shutil.which("x-terminal-emulator") or shutil.which("qterminal") or shutil.which("xterm")
    if not term:
        print("no terminal emulator found; run ccadmin in a terminal instead", file=sys.stderr)
        return 1
    subprocess.Popen([term, "-e", sys.executable, str(Path(__file__).resolve())],
                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def main() -> int:
    if "--window" in sys.argv:
        return open_window()
    if not sys.stdout.isatty():
        print("ccadmin needs a terminal", file=sys.stderr)
        return 1
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
