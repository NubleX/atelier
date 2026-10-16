#!/usr/bin/env bash
# Links atelier, tbrowse and ccadmin into ~/.local/bin. With --skill, also links the text-browser skill.
# Creates symlinks only; replaces an existing link of the same name, never a real file.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin="$HOME/.local/bin"
mkdir -p "$bin"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1 ($2)" >&2; missing=1; }; }
missing=0
need tmux "tmux 3.4 or newer"
need python3 "Python 3.10 or newer"
need claude "Claude Code"
python3 -c 'import prompt_toolkit, psutil' 2>/dev/null || { echo "missing python packages: pip install -r requirements.txt" >&2; missing=1; }
command -v lynx >/dev/null 2>&1 || echo "note: lynx not found, tbrowse will not work (optional)"
[ "$missing" = 0 ] || exit 1

link() {
  if [ -e "$2" ] && [ ! -L "$2" ]; then echo "skip $2: a real file is there" >&2; return; fi
  ln -sfn "$1" "$2" && echo "linked $2"
}
link "$here/bin/atelier" "$bin/atelier"
link "$here/bin/tbrowse" "$bin/tbrowse"
link "$here/tools/ccadmin.py" "$bin/ccadmin"

if [ "${1:-}" = "--skill" ]; then
  mkdir -p "$HOME/.claude/skills"
  link "$here/skills/text-browser" "$HOME/.claude/skills/text-browser"
fi
case ":$PATH:" in *":$bin:"*) ;; *) echo "note: add $bin to your PATH" ;; esac
echo "done. run: atelier"
