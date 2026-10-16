#!/usr/bin/env bash
# Removes duplicate skills and stale plugin cache. Dry run by default; pass --apply to delete.
# Backups of everything touched are in this directory (agents-skills.tgz, claude-skills-links.tgz).
set -euo pipefail

APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
run() { if [ "$APPLY" = 1 ]; then rm -rf -- "$@"; else printf 'would remove %s\n' "$@"; fi; }

LOCK="$HOME/.agents/.skill-lock.json"
SKILLS_ASSET="$HOME/.agents/skills"
SKILLS_LINK="$HOME/.claude/skills"

# 1. skills to drop: every cloudflare/skills and helius-labs/core-ai lock entry, plus helius and the local deja-history
mapfile -t DROP < <(python3 -I - "$LOCK" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))["skills"]
print("\n".join(k for k, v in s.items() if v.get("source") in ("cloudflare/skills", "helius-labs/core-ai")))
PY
)
DROP+=(helius)

for n in "${DROP[@]}"; do
  run "$SKILLS_LINK/$n" "$SKILLS_ASSET/$n"
done
run "$SKILLS_LINK/deja-history" "$SKILLS_ASSET/deja-history"

if [ "$APPLY" = 1 ]; then
  python3 -I - "$LOCK" "${DROP[@]}" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
for n in sys.argv[2:]: d["skills"].pop(n, None)
json.dump(d, open(p, "w"), indent=2)
PY
fi

# 2. plugin cache: delete every version dir that installed_plugins.json does not point at
CACHE="$HOME/.claude/plugins/cache"
mapfile -t STALE < <(python3 -I - "$HOME/.claude/plugins/installed_plugins.json" "$CACHE" <<'PY'
import json, os, sys
inst = json.load(open(sys.argv[1]))["plugins"]; cache = sys.argv[2]
keep = {e["installPath"] for v in inst.values() for e in v}
for mk in sorted(os.listdir(cache)):
    mp = os.path.join(cache, mk)
    if mk.startswith("temp_subdir_"): print(mp); continue
    for pl in sorted(os.listdir(mp)):
        pp = os.path.join(mp, pl)
        for ver in sorted(os.listdir(pp)):
            vp = os.path.join(pp, ver)
            if vp not in keep: print(vp)
PY
)
for p in "${STALE[@]}"; do run "$p"; done
[ "$APPLY" = 1 ] || echo "dry run: re-run with --apply"
