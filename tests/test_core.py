"""Tests for the harness core: project-folder encoding, tmux config rendering, wordmark."""
from __future__ import annotations

import unittest

from atelier.state import encode_project_dir
from atelier.theme import clip, fmt_age, fmt_size, lerp_color, wordmark
from atelier.tmuxconf import render


class Core(unittest.TestCase):
    def test_project_dir_encoding_matches_claude_code(self) -> None:
        self.assertEqual(encode_project_dir("/home/alex/.claude"), "-home-alex--claude")
        self.assertEqual(encode_project_dir("/home/alex/projects/site/.claude/worktrees/x"),
                         "-home-alex-projects-site--claude-worktrees-x")

    def test_tmux_conf_has_no_unresolved_tokens(self) -> None:
        conf = render("/opt/atelier/bin/atelier")
        self.assertNotIn("$A", conf)
        self.assertNotIn("$ACCENT", conf)
        self.assertIn('run-shell -b \'/opt/atelier/bin/atelier layout\'', conf)
        self.assertIn("#e879b8", conf)

    def test_wordmark_is_six_rows_of_equal_width(self) -> None:
        rows = wordmark()
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({len(r) for r in rows}), 1)

    def test_formatting_helpers(self) -> None:
        self.assertEqual(clip("abcdef", 4), "abc…")
        self.assertEqual(clip("abc", 0), "")
        self.assertEqual(fmt_size(1536), "1.5K")
        self.assertEqual(fmt_age(7200), "2h")
        self.assertEqual(lerp_color("#000000", "#ffffff", 0.5), "#808080")


if __name__ == "__main__":
    unittest.main()
