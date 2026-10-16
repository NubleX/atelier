---
name: text-browser
description: Read web pages and search the web as plain text with `tbrowse` (lynx). Faster and cheaper than WebFetch for docs, articles, search results and link lists, and subagents can use it directly. Use when you need page text, a list of links on a page, or a quick search, and the page does not need JavaScript.
---

# text-browser

`tbrowse` fetches pages through lynx: no JavaScript, no images, just text, so it is fast and cheap on tokens.

```
tbrowse https://example.com/docs/page          # page text, then a numbered link list
tbrowse links https://example.com/docs         # only the links, one per line
tbrowse search kali linux lynx text browser    # DuckDuckGo lite results
tbrowse --max 60000 URL                        # raise the 20000 character cap
```

## When to use it

- Documentation, READMEs, articles, changelogs, RFCs, search results: yes.
- To find the right page: `tbrowse search ...`, then `tbrowse URL` on the best result.
- To follow a site's structure: `tbrowse links URL`, then fetch the pages you need.
- Subagents: give them `tbrowse` in the prompt; it needs no MCP server and no login.

## When not to

- Pages that render with JavaScript come back nearly empty. Say so, then try the page's `.md`, `/raw`, API or RSS URL, or fall back to WebFetch.
- It identifies itself as lynx. If a site answers with a bot check or a login wall, report that plainly. Do not try to disguise the client or defeat the check.
- Never send secrets in a URL.

## With headless Ghidra

Binary analysis is the same idea: no GUI, text out. See the `ghidra` skill for `analyzeHeadless` (installed at `/usr/share/ghidra/support/analyzeHeadless`). In the atelier dash panel, `tbrowse` and `analyzeHeadless` commands are tagged `[text-web]` and `[ghidra]` so the user can watch them run.
