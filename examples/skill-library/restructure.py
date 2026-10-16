#!/usr/bin/env python3
"""Moves skills into ~/.claude/library/<department>/<skill>, merges small skills into bundles,
and symlinks ~/.agents/skills/<name> and ~/.claude/skills/<name> at the library.
Usage: restructure.py [--apply]. Dry run by default. Refuses to overwrite anything."""
import json
import os
import re
import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
HOME = Path.home()
LIB = HOME / ".claude" / "library"
AGENTS = HOME / ".agents" / "skills"
CLAUDE = HOME / ".claude" / "skills"
LOCK = HOME / ".agents" / ".skill-lock.json"

SINGLES = {
    "solana": ["solana-dev", "solana-kit", "solana-tx-building", "magicblock", "squads", "surfpool", "qedgen",
               "jito-bundles", "dex-execution", "integrating-jupiter", "pumpfun", "use-usdc", "lobstercash",
               "solana-payments-wallets-trading"],
    "trading": ["hummingbot", "trading-bot-architecture"],
    "onchain": ["dune"],
    "security": ["cybersecurity-analyst", "security-and-hardening", "meme-coin-audit", "solana-vulnerability-scanner", "ghidra"],
    "dev-workflow": ["gravitas", "improve-codebase-architecture", "principle-subtract-before-you-add", "find-skills", "opentui"],
    "_parked": ["deja-search", "meko-mcp-tools"],
}

BUNDLES = {
    "quant-toolkit": ("trading", [
        "backtesting-frameworks", "backtesting-trading-strategies", "backtrader", "cointegration-analysis",
        "correlation-analysis", "custom-indicators", "exit-strategies", "feature-engineering", "mean-reversion",
        "portfolio-analytics", "position-sizing", "regime-detection", "risk-management", "signal-classification",
        "slippage-modeling", "strategy-framework", "ta-lib", "trade-journal", "volatility-modeling",
        "walk-forward-validation", "stock-analysis"],
        "Quantitative trading toolkit: strategy design, backtesting and walk-forward validation, indicators and TA-Lib, "
        "signals and exit rules, position sizing and risk management, volatility and regime detection, cointegration, "
        "mean reversion and correlation, portfolio analytics, slippage modeling, trade journaling and stock analysis. "
        "Use when designing, testing or evaluating a trading strategy or measuring returns and risk."),
    "onchain-analysis": ("onchain", [
        "copy-trading", "dex-pool-analysis", "liquidity-analysis", "market-microstructure", "mev-analysis",
        "sybil-detection", "token-holder-analysis", "wallet-profiling", "whale-tracking", "pumpfun-mechanics",
        "gmgn-contract-dd", "gmgn-dev-score", "gmgn-kline-pattern", "gmgn-market"],
        "On-chain and DEX analysis for Solana tokens and wallets: pool and liquidity mechanics, market microstructure, "
        "MEV and sybil detection, holder, whale and wallet profiling, copy-trading, pump.fun mechanics, and GMGN "
        "contract due diligence, dev score, kline patterns and token lookups. Use when vetting a token, wallet or "
        "launch, or reading orderflow."),
    "market-data-apis": ("onchain", [
        "birdeye-api", "coingecko-api", "defillama-api", "dexscreener-api", "helius-api", "solanatracker-api",
        "quicknode", "solana-rpc", "shredstream"],
        "Market and chain data API reference: Birdeye, CoinGecko, DefiLlama, DexScreener, Helius API, SolanaTracker, "
        "QuickNode, Solana RPC and Shredstream. Use when choosing an endpoint or fetching prices, OHLCV, TVL, holder "
        "or transaction data."),
}

LOOSE_CLAUDE_DIRS = {"overkill": "dev-workflow"}  # real dirs living in ~/.claude/skills


def frontmatter_description(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^description:\s*(.*?)(?=^\w[\w-]*:|^---)", text, re.S | re.M)
    if not m:
        return ""
    d = re.sub(r"\s+", " ", m.group(1)).strip().strip("\"'>|").strip()
    return d if len(d) <= 170 else d[:167].rsplit(" ", 1)[0] + "..."


def act(msg: str, fn=None) -> None:
    print(("do   " if APPLY else "plan ") + msg)
    if APPLY and fn:
        fn()


def move(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        raise SystemExit(f"refusing: {dst} exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dst)


def link(target: Path, at: Path) -> None:
    if at.is_symlink():
        os.remove(at)
    elif at.exists():
        raise SystemExit(f"refusing: {at} is a real path")
    at.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, at)


def main() -> None:
    sources = {}
    lock = json.loads(LOCK.read_text())
    for name, v in lock["skills"].items():
        sources[name] = v.get("source")

    for dept, names in SINGLES.items():
        for n in names:
            src = AGENTS / n
            if not src.is_dir() or src.is_symlink():
                print(f"skip {n}: not a real dir in .agents/skills")
                continue
            dst = LIB / dept / n
            act(f"{src} -> {dst}", lambda s=src, d=dst: move(s, d))
            if dept != "_parked":
                act(f"link .agents/{n}", lambda t=dst, a=AGENTS / n: link(t, a))
                act(f"link .claude/{n}", lambda t=AGENTS / n, a=CLAUDE / n: link(t, a))

    for n, dept in LOOSE_CLAUDE_DIRS.items():
        src, dst = CLAUDE / n, LIB / dept / n
        if src.is_dir() and not src.is_symlink():
            act(f"{src} -> {dst}", lambda s=src, d=dst: move(s, d))
            act(f"link .agents/{n}", lambda t=dst, a=AGENTS / n: link(t, a))
            act(f"link .claude/{n}", lambda t=AGENTS / n, a=CLAUDE / n: link(t, a))

    for bundle, (dept, members, description) in BUNDLES.items():
        root = LIB / dept / bundle
        rows = []
        for n in members:
            src = AGENTS / n
            if not src.is_dir() or src.is_symlink():
                print(f"skip {n}: not a real dir in .agents/skills")
                continue
            rows.append((n, frontmatter_description(src / "SKILL.md")))
            act(f"{src} -> {root / 'references' / n}", lambda s=src, d=root / "references" / n: move(s, d))
            act(f"unlink .claude/{n}", lambda a=CLAUDE / n: os.remove(a) if a.is_symlink() else None)
        body = [
            "---", f"name: {bundle}", f"description: {description}", "---", "",
            f"# {bundle}", "",
            "Bundle of focused skills. Find the topic below, then read `references/<topic>/SKILL.md`",
            "(and any files beside it) before answering. Read only the topics the task needs.", "",
            "| Topic | Covers |", "| --- | --- |",
        ] + [f"| `{n}` | {d} |" for n, d in rows]

        def write(root=root, text="\n".join(body) + "\n") -> None:
            root.mkdir(parents=True, exist_ok=True)
            (root / "SKILL.md").write_text(text, encoding="utf-8")

        act(f"write {root / 'SKILL.md'} ({len(rows)} topics)", write)
        act(f"link .agents/{bundle}", lambda t=root, a=AGENTS / bundle: link(t, a))
        act(f"link .claude/{bundle}", lambda t=AGENTS / bundle, a=CLAUDE / bundle: link(t, a))

    def sources_file() -> None:
        merged = {m: sources.get(m, "local") for _, (_, ms, _) in BUNDLES.items() for m in ms}
        (LIB / "SOURCES.json").write_text(json.dumps(merged, indent=2) + "\n")

    act(f"write {LIB / 'SOURCES.json'} (origin of every merged member)", sources_file)

    def untrack() -> None:
        for _, (_, ms, _) in BUNDLES.items():
            for m in ms:
                lock["skills"].pop(m, None)
        LOCK.write_text(json.dumps(lock, indent=2))

    act("drop merged members from .skill-lock.json so `skills update` does not recreate them", untrack)


main()
