"""Deletion-safety tests for the system panel. Only temp directories are touched."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from atelier.panels.system import delete_contents, delete_old_transcripts, dir_size, safe_targets


class SafeTargets(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.cache = self.base / "a" / "b" / "cache"
        self.cache.mkdir(parents=True)
        self.outside = self.base / "a" / "b" / "precious"
        self.outside.mkdir()
        (self.outside / "keep.txt").write_text("keep")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_children_of_allowlisted_root(self) -> None:
        (self.cache / "x").write_text("1")
        (self.cache / "d").mkdir()
        names = sorted(p.name for p in safe_targets(self.cache, [self.cache]))
        self.assertEqual(names, ["d", "x"])

    def test_refuses_root_outside_allowlist(self) -> None:
        self.assertEqual(safe_targets(self.outside, [self.cache]), [])

    def test_refuses_shallow_allowlist_entries(self) -> None:
        self.assertEqual(safe_targets(Path("/home"), [Path("/home")]), [])
        self.assertEqual(safe_targets(Path("/"), [Path("/")]), [])

    def test_never_returns_root_itself(self) -> None:
        self.assertNotIn(self.cache, safe_targets(self.cache, [self.cache]))

    def test_symlink_escaping_allowlist_is_left_alone(self) -> None:
        os.symlink(self.outside, self.cache / "escape")
        self.assertEqual(safe_targets(self.cache, [self.cache]), [])

    def test_root_that_is_a_symlink_out_of_the_allowlist_is_refused(self) -> None:
        link = self.base / "a" / "b" / "link"
        os.symlink(self.outside, link)
        self.assertEqual(safe_targets(link, [self.cache]), [])

    def test_delete_contents_keeps_root_and_never_follows_links(self) -> None:
        (self.cache / "f").write_text("12345")
        sub = self.cache / "sub"
        sub.mkdir()
        (sub / "g").write_text("678")
        os.symlink(self.outside, sub / "inner-link")  # rmtree must unlink it, not recurse into it
        result = delete_contents(self.cache, [self.cache])
        self.assertTrue(self.cache.is_dir())
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertTrue((self.outside / "keep.txt").is_file())
        self.assertEqual(result.errors, 0)

    def test_delete_contents_outside_allowlist_does_nothing(self) -> None:
        delete_contents(self.outside, [self.cache])
        self.assertTrue((self.outside / "keep.txt").is_file())

    def test_dir_size_counts_files_not_links(self) -> None:
        (self.cache / "f").write_bytes(b"x" * 100)
        os.symlink(self.outside, self.cache / "l")
        self.assertEqual(dir_size(self.cache), 100)

    def test_old_transcripts_only_removes_old_jsonl(self) -> None:
        proj = self.base / "a" / "b" / "projects" / "-p"
        proj.mkdir(parents=True)
        old, new, other = proj / "old.jsonl", proj / "new.jsonl", proj / "notes.txt"
        for f in (old, new, other):
            f.write_text("x")
        ancient = time.time() - 90 * 86400
        os.utime(old, (ancient, ancient))
        os.utime(other, (ancient, ancient))
        delete_old_transcripts(proj.parent, [proj.parent], time.time())
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
