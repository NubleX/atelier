# skill-library examples

These are the actual scripts used to restructure one machine. They contain that machine's skill names, so read
and edit before running. Both are safe by default.

- `restructure.py`: moves skills into `~/.claude/library/<department>/`, merges groups into bundles and relinks
  `~/.agents/skills` and `~/.claude/skills`. Dry run unless `--apply`; refuses to overwrite anything.
- `remove-dupes.sh`: removes named duplicate skills and stale plugin cache versions. Dry run unless `--apply`.

Back up first: `tar -C ~/.agents -czf skills.tgz skills; tar -C ~/.claude -czf links.tgz skills`.
