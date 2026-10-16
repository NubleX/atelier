"""Aurelia colors and shared formatting for every atelier panel.

Palette values come from the Aurelia skin of the local `skins` mod (aurelia.ts); `bg` and `bg_alt` are
the near-black violet ground chosen here because that skin leaves the ground to the terminal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from prompt_toolkit.styles import Style


@dataclass(frozen=True)
class Palette:
    bg: str = "#150b22"
    bg_alt: str = "#1d1030"
    zebra: str = "#2a183e"
    surface: str = "#36204c"
    fg: str = "#e8d7fc"
    muted: str = "#9a85bd"
    read: str = "#d7c0fc"
    write: str = "#e879b8"
    run: str = "#3dd5e6"
    search: str = "#bc96ee"
    web: str = "#6cb4f7"
    mcp: str = "#d946a8"
    other: str = "#c8aaf0"
    ok: str = "#22d36e"
    err: str = "#e91e63"
    warn: str = "#ffc400"


AURELIA = Palette()

# (style class, text) pairs as prompt_toolkit's FormattedText expects them
Fragments = list[tuple[str, str]]


def style() -> Style:
    """prompt_toolkit style used by every panel. Class names are the vocabulary panels draw with."""
    p = AURELIA
    return Style.from_dict({
        "": f"fg:{p.fg} bg:{p.bg}",
        "title": f"fg:{p.write} bold",
        "accent": f"fg:{p.write}",
        "magenta": f"fg:{p.mcp}",
        "muted": f"fg:{p.muted}",
        "dim": f"fg:{p.muted}",
        "ok": f"fg:{p.ok}",
        "err": f"fg:{p.err}",
        "warn": f"fg:{p.warn}",
        "read": f"fg:{p.read}",
        "write": f"fg:{p.write}",
        "run": f"fg:{p.run}",
        "search": f"fg:{p.search}",
        "web": f"fg:{p.web}",
        "mcp": f"fg:{p.mcp}",
        "other": f"fg:{p.other}",
        "zebra": f"bg:{p.zebra}",
        "sel": f"fg:{p.bg} bg:{p.write} bold",
        "btn": f"fg:{p.fg} bg:{p.surface}",
        "btn.on": f"fg:{p.bg} bg:{p.run} bold",
        "btn.danger": f"fg:{p.fg} bg:{p.err} bold",
        "bar": f"fg:{p.muted} bg:{p.bg_alt}",
        "border": f"fg:{p.surface}",
    })


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def lerp_color(a: str, b: str, t: float) -> str:
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return "#{:02x}{:02x}{:02x}".format(round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def gradient(text: str, start: str = AURELIA.search, end: str = AURELIA.write, extra: str = "") -> Fragments:
    """One fragment per character, blended start to end. `extra` is appended to each style (e.g. 'bold')."""
    n = max(1, len(text) - 1)
    return [(f"fg:{lerp_color(start, end, i / n)} {extra}".strip(), ch) for i, ch in enumerate(text)]


def fmt_size(n: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def fmt_age(seconds: float) -> str:
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def clip(text: str, width: int) -> str:
    """Cuts to `width` cells with an ellipsis; width <= 0 gives ''."""
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def join_cols(cols: Sequence[tuple[str, int]]) -> str:
    """Pads/clips (text, width) pairs and concatenates them, for aligned rows."""
    return "".join(clip(t, w).ljust(w) for t, w in cols)


_GLYPHS = {
    "A": (" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"),
    "T": ("████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "),
    "E": ("███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"),
    "L": ("██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"),
    "I": ("██╗", "██║", "██║", "██║", "██║", "╚═╝"),
    "R": ("██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"),
}


def wordmark(word: str = "ATELIER") -> tuple[str, ...]:
    """Block-letter banner, six rows tall, built from the glyphs above."""
    return tuple("".join(_GLYPHS[ch][row] for ch in word) for row in range(6))
