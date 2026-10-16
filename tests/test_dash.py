"""Tests for the dash panel's transcript parsing. Synthetic lines only, shaped like Claude Code transcripts."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from atelier.panels.dash import DashState, Tracker, bash_tags, classify_tool, encode_project_dir, fmt_tokens, summarize_tool


def assistant(msg_id: str, blocks: list[dict], usage: dict | None = None, ts: str = "2026-10-16T02:00:00.000Z") -> str:
    msg = {"id": msg_id, "model": "claude-sonnet-5-5", "content": blocks,
           "usage": usage or {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20}}
    return json.dumps({"type": "assistant", "timestamp": ts, "message": msg})


def result(tool_id: str, error: bool = False) -> str:
    return json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "is_error": error}]}})


def tool(tool_id: str, name: str, **inp: object) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


class Helpers(unittest.TestCase):
    def test_encoding_and_formatting(self) -> None:
        self.assertEqual(encode_project_dir("/home/alex/.claude"), "-home-alex--claude")
        self.assertEqual(fmt_tokens(950), "950")
        self.assertEqual(fmt_tokens(12300), "12.3k")
        self.assertEqual(fmt_tokens(1_200_000), "1.2M")

    def test_classes_and_summaries(self) -> None:
        self.assertEqual(classify_tool("Bash"), "run")
        self.assertEqual(classify_tool("Edit"), "write")
        self.assertEqual(classify_tool("mcp__deja__deja"), "mcp")
        self.assertEqual(classify_tool("WebFetch"), "web")
        self.assertEqual(summarize_tool("Bash", {"command": "ls -la\nsecond line"}, "/x"), "ls -la")
        self.assertIn("a.py", summarize_tool("Read", {"file_path": "/x/src/a.py"}, "/x"))
        self.assertNotIn("/x/", summarize_tool("Read", {"file_path": "/x/src/a.py"}, "/x"))

    def test_tool_tags(self) -> None:
        self.assertIn("text-web", bash_tags("tbrowse example.com"))
        self.assertIn("ghidra", bash_tags("analyzeHeadless /tmp/proj"))
        self.assertEqual(bash_tags("ls"), "")


class Parsing(unittest.TestCase):
    def test_tool_lifecycle_and_usage_dedup(self) -> None:
        st = DashState(cwd="/x")
        lines = [
            assistant("m1", [tool("t1", "Bash", command="git status")]),
            assistant("m1", [tool("t2", "Read", file_path="/x/a.py")]),  # same API message, second content block
            result("t1"),
            result("t2", error=True),
        ]
        st.feed(lines)
        self.assertEqual([a.status for a in st.activity], ["ok", "err"])
        self.assertEqual(st.model, "claude-sonnet-5-5")
        self.assertEqual(st.totals.output, 5)  # counted once although the message spans two lines
        self.assertEqual(st.last_usage.context, 130 if st.last_usage else -1)

    def test_unfinished_tool_stays_running(self) -> None:
        st = DashState(cwd="/x")
        st.feed([assistant("m1", [tool("t1", "Bash", command="sleep 9")])])
        self.assertEqual(st.activity[0].status, "run")

    def test_tolerates_garbage(self) -> None:
        st = DashState(cwd="/x")
        st.feed(["", "not json", "[1,2]", json.dumps({"type": "mystery"}), b"\xff\xfe"])
        self.assertEqual(st.activity, [])


class Incremental(unittest.TestCase):
    def test_partial_lines_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "s.jsonl"
            first = assistant("m1", [tool("t1", "Bash", command="a")])
            second = assistant("m2", [tool("t2", "Bash", command="b")])
            f.write_text(first + "\n" + second[:25])  # second line is cut mid-way
            tr = Tracker("/x")
            tr.poll(f, "/x")
            self.assertEqual(len(tr.state.activity), 1)
            with f.open("a") as fh:
                fh.write(second[25:] + "\n")
            tr.poll(f, "/x")
            self.assertEqual(len(tr.state.activity), 2)
            f.write_text(first + "\n")  # truncated and rewritten: state restarts
            tr.poll(f, "/x")
            self.assertEqual(len(tr.state.activity), 1)


if __name__ == "__main__":
    unittest.main()
