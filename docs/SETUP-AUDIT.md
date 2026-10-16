# Untangling a cluttered Claude Code setup

What this project grew out of: a machine with ~190 skills, 39 plugins, duplicate installs of the same tools,
a mouse that stopped working, and repos that seemed not to see their skills. Everything below was found with
read-only commands first; each fix has a backup. Paths use `~`; adapt to your machine.

## 1. Where things actually come from

Claude Code merges several sources, and they overlap easily:

| Source | Lives in | Typical surprise |
| --- | --- | --- |
| user skills | `~/.claude/skills/<name>/SKILL.md` | only one folder level is scanned, so department subfolders do not work there |
| `npx skills` installs | `~/.agents/skills` + lock `~/.agents/.skill-lock.json`, symlinked into `~/.claude/skills` | the same skill pack can also arrive as a plugin |
| plugin skills | `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/skills` | shown as `plugin:skill`, so a duplicate does not collide, it just appears twice |
| plugin state | `~/.claude/plugins/installed_plugins.json`, `enabledPlugins` in `settings.json` | installed-but-disabled plugins still show up in managers |
| MCP servers | `~/.claude.json` (user and per-project "local" scope), project `.mcp.json`, plugins | `~/.claude/mcp.json` is not a path Claude Code reads |

## 2. Findings worth checking on your machine

- **Duplicate skill packs.** A plugin and `npx skills` shipped the same set. Compare `ls ~/.claude/skills`
  with `ls ~/.claude/plugins/cache/*/*/*/skills`.
- **Two installs of one tool.** A standalone installer and its Claude plugin both registered hooks, a skill and
  an MCP server. Keep one: `claude plugin uninstall <plugin>`, `claude plugin marketplace remove <name>`.
- **A mouse variable.** `CLAUDE_CODE_DISABLE_MOUSE=1` in `settings.json` `env` *and* exported from `~/.zshrc`
  disables scroll and every clickable mod button. Remove both; an old shell keeps the old value.
- **Repo-level hiding.** `skillOverrides` in a repo's `.claude/settings.local.json` (values `off`,
  `name-only`, `user-invocable-only`) hides skills in that repo only, which is why some repos "do not see"
  skills. Grep: `grep -l skillOverrides ~/projects/*/.claude/settings*.json`.
- **Skill list too long.** With a couple of hundred skills, many are listed name-only and never auto-trigger.
  Merging small related skills into one bundle (below) cuts the list.
- **Stale plugin cache.** Old version folders pile up under `plugins/cache`. Keep exactly the `installPath`
  entries from `installed_plugins.json`.
- **Dangling settings.** `enabledPlugins` entries for a marketplace that no longer exists; a duplicate
  `pluginConfigs` key.
- **Disable flags that outlive their cause.** `disabledMcpServers` per project in `~/.claude.json`.

## 3. A layout that holds

Real files live in one library, grouped by department; both tool folders are symlinks into it:

```
~/.claude/library/<department>/<skill>/        real files
~/.agents/skills/<skill>  ->  library path     for npx skills and other agents
~/.claude/skills/<skill>  ->  ~/.agents/skills/<skill>
```

Small related skills can be merged into one bundle: a `SKILL.md` with a table of topics and the originals kept
under `references/<topic>/`. 44 skills became 3 bundles here. See `examples/skill-library/restructure.py`
(dry run by default, refuses to overwrite) and `remove-dupes.sh`. Bundled members are dropped from the lock so
`npx skills update` does not recreate them; `SOURCES.json` records where each came from.

## 4. Order that kept it safe

1. Read-only inventory (the tables above).
2. Back up every config and tar the skill folders.
3. Upgrade tracked skills first (`npx skills update -g -y <names>`; in zsh, expand the list with `${=NAMES}`),
   because afterwards the layout is no longer what the updater expects.
4. Remove duplicates with a dry-run script, then `--apply`.
5. Restructure, verify there are no broken symlinks, restart Claude Code.

## 5. Tooling that came out of it

`tools/ccadmin.py` lists plugins, skills and MCP servers with mouse and keyboard and toggles or removes them
through `claude plugin ...`, `claude mcp remove` and `skillOverrides`. Skills are only ever unlinked, never
erased from the library.
