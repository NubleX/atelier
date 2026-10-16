#!/usr/bin/env python3
"""Draws docs/logo.svg: the welcome-screen wordmark inside a mock tab bar, in the Aurelia palette.

Every block and box-drawing cell is drawn as shapes, so the banner needs no font. Run from the repo root:
    python3 tools/make_logo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atelier.theme import AURELIA as P, wordmark  # noqa: E402

CELL_W, CELL_H = 15, 28
D = 3.2  # half the gap between the two lines of a double box-drawing stroke
W, H = 1000, 372
BAR_H = 34
FONT = "'JetBrains Mono','DejaVu Sans Mono',Menlo,Consolas,monospace"


def _lines(ch: str, x: float, y: float) -> list[str]:
    """Double-line box-drawing glyphs as SVG path data, in a cell whose top-left corner is (x, y)."""
    cx, cy = x + CELL_W / 2, y + CELL_H / 2
    r, b = x + CELL_W, y + CELL_H
    paths = {
        "═": [f"M{x},{cy - D}H{r}", f"M{x},{cy + D}H{r}"],
        "║": [f"M{cx - D},{y}V{b}", f"M{cx + D},{y}V{b}"],
        "╔": [f"M{cx - D},{b}V{cy - D}H{r}", f"M{cx + D},{b}V{cy + D}H{r}"],
        "╗": [f"M{x},{cy - D}H{cx + D}V{b}", f"M{x},{cy + D}H{cx - D}V{b}"],
        "╚": [f"M{cx - D},{y}V{cy + D}H{r}", f"M{cx + D},{y}V{cy - D}H{r}"],
        "╝": [f"M{cx + D},{y}V{cy + D}H{x}", f"M{cx - D},{y}V{cy - D}H{x}"],
    }
    return paths.get(ch, [])


def build() -> str:
    rows = wordmark()
    cols = len(rows[0])
    mark_w, mark_h = cols * CELL_W, len(rows) * CELL_H
    x0, y0 = (W - mark_w) / 2, BAR_H + 44
    blocks: list[str] = []
    strokes: list[str] = []
    for ri, row in enumerate(rows):
        for ci, ch in enumerate(row):
            x, y = x0 + ci * CELL_W, y0 + ri * CELL_H
            if ch == "█":
                blocks.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{CELL_W}" height="{CELL_H - 1.5}"/>')
            else:
                strokes.extend(_lines(ch, x, y))
    tagline_y = y0 + mark_h + 38
    keys_y = H - 16
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="atelier">
  <title>atelier</title>
  <defs>
    <linearGradient id="g" gradientUnits="userSpaceOnUse" x1="{x0:.0f}" y1="0" x2="{x0 + mark_w:.0f}" y2="0">
      <stop offset="0" stop-color="{P.search}"/>
      <stop offset="1" stop-color="{P.write}"/>
    </linearGradient>
    <linearGradient id="glow" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{P.bg_alt}"/>
      <stop offset="1" stop-color="{P.bg}"/>
    </linearGradient>
    <clipPath id="win"><rect width="{W}" height="{H}" rx="14"/></clipPath>
  </defs>
  <g clip-path="url(#win)">
    <rect width="{W}" height="{H}" fill="url(#glow)"/>
    <rect width="{W}" height="{BAR_H}" fill="{P.bg}"/>
    <g font-family="{FONT}" font-size="15">
      <text x="18" y="22" fill="{P.write}" font-weight="700">◆ atelier</text>
      <text x="108" y="22" fill="{P.surface}">│</text>
      <rect x="126" y="6" width="84" height="23" rx="3" fill="{P.surface}"/>
      <text x="136" y="22" fill="{P.write}" font-weight="700">1 home</text>
      <text x="{W - 310}" y="22" fill="{P.run}">+ tab</text>
      <text x="{W - 242}" y="22" fill="{P.web}">system</text>
      <text x="{W - 168}" y="22" fill="{P.search}">manage</text>
      <text x="{W - 86}" y="22" fill="{P.muted}">│ 05:11</text>
    </g>
    <g fill="none" stroke="{P.search}" stroke-opacity="0.55" stroke-width="1.6" stroke-linecap="square">
      <path d="{' '.join(strokes)}"/>
    </g>
    <g fill="url(#g)" shape-rendering="crispEdges">{''.join(blocks)}</g>
    <g font-family="{FONT}" text-anchor="middle">
      <text x="{W / 2}" y="{tagline_y:.0f}" font-size="17" fill="{P.fg}">One calm harness around real Claude Code</text>
      <text x="{W / 2}" y="{tagline_y + 24:.0f}" font-size="13" fill="{P.muted}">tabs that remember · git-aware work tree · model dash · system tab</text>
      <text x="{W / 2}" y="{keys_y}" font-size="12" fill="{P.muted}" opacity="0.8">F1 home   F2 system   F3 new tab   F7 manage   F5/F6 panels</text>
    </g>
  </g>
  <rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="13" fill="none" stroke="{P.surface}" stroke-width="2"/>
</svg>
"""


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "docs" / "logo.svg"
    out.write_text(build(), encoding="utf-8")
    print(out)
