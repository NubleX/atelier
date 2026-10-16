# Architecture

## The decision: tmux is the terminal engine

Claude Code's own interface uses modern terminal features: truecolor, modified keys, mouse reporting, and
mods that draw bands and panes. Embedding it in a custom terminal widget means re-implementing a terminal
emulator, and every gap shows up as a rendering bug in the thing you look at all day.

So atelier does not emulate anything. A dedicated tmux server (`tmux -L atelier`) runs `claude` in a real
terminal, and atelier is everything around it:

```
atelier (CLI)  ──►  tmux server "atelier"  ──►  window per tab
                                                  ├─ pane role=tree    python -m atelier panel tree
                                                  ├─ pane role=claude  claude [--resume <id>]
                                                  └─ pane role=dash    python -m atelier panel dash
```

Panes are tagged with a tmux pane option `@role`; windows carry `@kind` (`home`, `work`, `system`), `@cwd`,
`@session` and `@created`. Those options are the only shared state between processes.

## Modules

| File | Job |
| --- | --- |
| `atelier/cli.py` | commands; start/restore; the layout and save logic |
| `atelier/tmuxctl.py` | thin wrapper over the tmux CLI (`ATELIER_SOCKET` overrides the socket name) |
| `atelier/tmuxconf.py` | generates `tmux.conf`: Aurelia theme, F-keys, mouse bindings, hooks |
| `atelier/state.py` | `~/.config/atelier/state.json`, transcript lookup, the save lock |
| `atelier/theme.py` | palette, prompt_toolkit style, gradient, formatters, banner |
| `atelier/projects.py` | project discovery for the welcome screen and picker |
| `atelier/panels/*.py` | welcome, pick, tree, dash, system: each a prompt_toolkit full-screen app |
| `bin/tbrowse` | lynx wrapper for agents |
| `tools/ccadmin.py` | curses manager for plugins, skills and MCP servers |
| `tools/make_logo.py` | draws `docs/logo.svg` from the palette and the welcome wordmark |

## Layout (responsive)

`layout_window` is idempotent. Given the window width it decides which side panels should exist (tree from
100 columns, dash from 150, minus anything toggled off with F5/F6 in the `@hide` option), kills the ones that
should not, splits in the missing ones with `split-window -d` (so focus stays on Claude), and restores their
widths (remembered in `layout.json`, capped so Claude keeps 60 columns) if they drifted. A border drag ends in
`atelier snap`, which saves the new widths, snaps them to presets and closes a panel dragged under 14 columns. It runs from tmux hooks: `client-resized`, `client-attached`, `after-select-window`.

## Persistence

`save_state` runs from hooks (`after-new-window`, `window-renamed`, `window-unlinked`) and from `#(atelier tick)`
in the status line every 15 seconds. Per work window it records name, folder and session id. The session id is
found by taking the newest `*.jsonl` in `~/.claude/projects/<folder with non-alphanumerics replaced by '-'>/`
that no other tab holds. Rules that keep it from destroying state:

- a flock makes concurrent saves skip rather than race;
- if no work windows exist, nothing is written (a server shutting down must not wipe the remembered tabs),
  except from the explicit close command.

## Panel contract

A panel is a module with `main() -> int`, run by `atelier panel <name>`. Environment it may read:

| Variable | Meaning |
| --- | --- |
| `ATELIER_WIN` | the tmux window id (`@3`) the panel belongs to |
| `ATELIER_SOCKET` | tmux socket name, default `atelier` |
| `ATELIER_CONFIG_DIR` | state directory, default `~/.config/atelier` |
| `ATELIER_CWD` | override the project folder (used by tests) |

To talk to Claude, a panel finds the pane whose `@role` is `claude` and uses `tmux send-keys -l`. That is how
the work tree inserts `@path` and the dash switches models.

## The dash reads transcripts

Claude Code writes every session to `~/.claude/projects/<encoded cwd>/<session>.jsonl`. The dash tails that
file incrementally (remembers its offset, tolerates a half-written last line, restarts on truncation). One API
message is written as several lines that repeat the same `usage`, so usage is de-duplicated by message id.
It reports counts only; it does not estimate cost.

## Testing without a terminal

The panels cannot be exercised by plain unit tests, so they are checked in a detached tmux:

```
tmux -L t new-session -d -x 110 -y 32 'atelier panel system'
tmux -L t send-keys 2
tmux -L t capture-pane -p
```

Set `ATELIER_SOCKET` and `ATELIER_CONFIG_DIR` and put a stub `claude` first on `PATH` to run the whole harness
without touching your real setup. Unit tests cover the pure logic: transcript parsing, deletion safety, helpers.

## Not done yet

- the F7/F3 popups were not exercised by a human in a real terminal
- a command palette, per-tab activity dots, a git-aware tab title
- an Ori/Hermes-style skin system (the palette is one dataclass today)
