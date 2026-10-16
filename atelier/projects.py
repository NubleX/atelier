"""Finds the projects worth offering on the welcome screen and in the new-tab picker."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atelier.state import last_active

PROJECTS_DIR = Path.home() / "projects"


@dataclass(frozen=True)
class Project:
    name: str
    path: str
    branch: str
    active: float  # epoch seconds of the last sign of life


def git_branch(path: Path) -> str:
    try:
        head = (path / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return head.removeprefix("ref: refs/heads/") if head.startswith("ref:") else head[:7]


def discover(limit: int = 40) -> list[Project]:
    found: list[Project] = []
    try:
        entries = sorted(p for p in PROJECTS_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return found
    for p in entries:
        found.append(Project(p.name, str(p), git_branch(p), last_active(str(p))))
    found.sort(key=lambda x: x.active, reverse=True)
    return found[:limit]
