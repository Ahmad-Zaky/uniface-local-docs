#!/usr/bin/env python3
"""
Uniface 10.4 docs scraper (Rocket Software / Zoomin).

The docs site is JS-rendered, so we use Playwright (headless Chromium) to:
  1. Open the start URL
  2. Wait for the sidebar TOC to render, then recursively expand every node
  3. Extract the full TOC tree (with hierarchy preserved)
  4. Visit every leaf page and save its main content

Output structure:
  data/
    toc.json                 # full hierarchical TOC
    pages/<page_id>.json     # { id, title, breadcrumbs, html, text, url }
    state.json               # resume state: visited page IDs

Polite by default: 600 ms between page fetches. Tweak with --delay.

USAGE
    python -m playwright install chromium     # one-time
    pip install playwright beautifulsoup4 lxml

    # 1. Discovery - dump TOC + a sample page, no full crawl. Use this first
    #    to confirm selectors are working.
    python scrape.py --discover

    # 2. Full crawl
    python scrape.py

    # Resume after interruption (skips already-scraped pages)
    python scrape.py --resume

    # English instead of German
    python scrape.py --lang en

NOTE ON SELECTORS
    Zoomin's DOM can change. If the scraper finds zero TOC items, run
    `python scrape.py --discover --debug` to dump the rendered HTML and
    inspect with browser devtools, then adjust the SELECTORS dict below.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup  # type: ignore
from playwright.sync_api import (  # type: ignore
    Page,
    Playwright,
    TimeoutError as PWTimeout,
    sync_playwright,
)

# ────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────

BASE = "https://docs.rocketsoftware.com"
BUNDLE = "uniface_104"

# Starting page (root of the Uniface library). Adjust if you want to anchor on
# a different node — the scraper will use the TOC discovered there.
START_PAGE_ID = "etd1665702764995"

# CSS selectors — Zoomin DOM. These are best-effort defaults; the script
# falls back to multiple alternatives if the primary doesn't match.
SELECTORS = {
    # The whole left-side TOC container. Several Zoomin themes have used
    # different class names over time; we try each in order.
    "toc_root": [
        "div.zDocsToc",          # Zoomin 2024+ (Rocket Software docs)
        "ul.zDocsTocList",       # inner list when wrapper div is absent
        "nav.toc",
        "aside nav",
        "div.toc",
        "div[class*='toc']",
        "nav[aria-label*='Table' i]",
        "ul.toc-list",
    ],
    # Individual TOC list items (each is one topic, possibly with children).
    "toc_item": [
        "li[data-testid='tocItem']",  # Zoomin 2024+
        "li.toc-item",
        "li[class*='toc']",
        "li:has(> a)",
    ],
    # Expand/collapse handle on a TOC item that has children.
    "toc_expander": [
        "button.zDocsTocCollapseItemButton",  # Zoomin 2024+
        "button.toc-expand",
        "button[aria-expanded]",
        "span.toc-toggle",
        "i.toc-toggle",
    ],
    # Main content area on a topic page.
    "content_root": [
        "div.zDocsTopicPageBody",      # Zoomin 2024+ full body (head + content)
        "main article",
        "main",
        "div.topic-content",
        "div[class*='content-body']",
        "article",
    ],
}

# Wait timeouts (ms)
WAIT_RENDER_MS = 30_000
WAIT_NETWORK_MS = 15_000


# ────────────────────────────────────────────────────────────────────
# Data classes
# ────────────────────────────────────────────────────────────────────


@dataclass
class TocNode:
    id: str
    title: str
    url: str
    children: list["TocNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "children": [c.to_dict() for c in self.children],
        }


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def page_url(page_id: str, lang: str) -> str:
    if lang == "en":
        return f"{BASE}/bundle/{BUNDLE}/page/{page_id}.html"
    return f"{BASE}/{lang}/bundle/{BUNDLE}/page/{page_id}.html"


def extract_page_id(href: str) -> str | None:
    m = re.search(r"/page/([^/]+?)\.html", href)
    return m.group(1) if m else None


def first_match(page: Page, selectors: list[str]) -> str | None:
    """Return the first CSS selector that finds at least one element."""
    for sel in selectors:
        try:
            if page.locator(sel).count() > 0:
                return sel
        except Exception:
            continue
    return None


def wait_for_toc(page: Page) -> str:
    """Wait until a TOC container has rendered. Return the matching selector."""
    try:
        page.wait_for_load_state("networkidle", timeout=WAIT_NETWORK_MS)
    except PWTimeout:
        pass  # site keeps background XHRs running; the selector loop below handles readiness
    deadline = time.time() + WAIT_RENDER_MS / 1000
    while time.time() < deadline:
        sel = first_match(page, SELECTORS["toc_root"])
        if sel:
            # Make sure it actually contains anchor links before declaring victory
            if page.locator(f"{sel} a").count() >= 3:
                return sel
        time.sleep(0.4)
    raise RuntimeError(
        "Could not locate TOC. Run with --debug to dump the rendered HTML "
        "and update SELECTORS['toc_root'] in scrape.py."
    )


def expand_all_toc_nodes(page: Page, toc_selector: str, max_passes: int = 12) -> None:
    """Repeatedly click every collapsed expander until none remain.

    The TOC is virtualised/lazy in some themes, so we do multiple passes.
    """
    for pass_no in range(max_passes):
        clicked = 0
        # Try aria-expanded="false" first — it's the most reliable signal
        try:
            collapsed = page.locator(
                f"{toc_selector} [aria-expanded='false']"
            ).element_handles()
        except Exception:
            collapsed = []

        # Fallback: try generic expander selectors
        if not collapsed:
            for sel in SELECTORS["toc_expander"]:
                try:
                    handles = page.locator(f"{toc_selector} {sel}").element_handles()
                    if handles:
                        collapsed = handles
                        break
                except Exception:
                    continue

        for handle in collapsed:
            try:
                handle.scroll_into_view_if_needed(timeout=1500)
                handle.click(timeout=1500)
                clicked += 1
                # Tiny wait — TOC nodes can lazy-load children
                page.wait_for_timeout(50)
            except Exception:
                continue

        print(f"  expand pass {pass_no + 1}: clicked {clicked}", file=sys.stderr)
        if clicked == 0:
            return
        page.wait_for_timeout(300)


def parse_toc_from_html(toc_html: str, lang: str) -> list[TocNode]:
    """Convert the rendered TOC HTML into a TocNode tree.

    Strategy: walk the nested <ul>/<li>/<a> structure. Each <li> that contains
    a direct <a> is a node. Nested <ul>s are children.
    """
    soup = BeautifulSoup(toc_html, "lxml")

    def walk(ul) -> list[TocNode]:
        nodes: list[TocNode] = []
        for li in ul.find_all("li", recursive=False):
            # Find the first anchor that points to a page
            anchor = None
            for a in li.find_all("a", recursive=False):
                anchor = a
                break
            if anchor is None:
                # Maybe the anchor is nested in a span/div wrapper
                anchor = li.find("a")
            if anchor is None:
                continue

            href = anchor.get("href") or ""
            title = anchor.get_text(strip=True)
            if not title:
                continue

            pid = extract_page_id(href)
            if pid is None:
                continue

            # Children: first nested <ul> inside this <li>
            children: list[TocNode] = []
            nested_ul = li.find("ul")
            if nested_ul is not None:
                children = walk(nested_ul)

            nodes.append(
                TocNode(
                    id=pid,
                    title=title,
                    url=page_url(pid, lang),
                    children=children,
                )
            )
        return nodes

    # Find the top-level <ul> inside the TOC
    top_ul = soup.find("ul")
    if top_ul is None:
        return []
    return walk(top_ul)


def load_toc_from_json(path: Path) -> list[TocNode]:
    """Deserialise toc.json back into TocNode objects."""
    def from_dict(d: dict) -> TocNode:
        return TocNode(
            id=d["id"],
            title=d["title"],
            url=d["url"],
            children=[from_dict(c) for c in d.get("children", [])],
        )
    return [from_dict(n) for n in json.loads(path.read_text(encoding="utf-8"))]


def collect_all_ids(nodes: list[TocNode]) -> list[TocNode]:
    """Flatten the tree into a list (parent-first, depth-first)."""
    out: list[TocNode] = []

    def rec(ns: list[TocNode]) -> None:
        for n in ns:
            out.append(n)
            rec(n.children)

    rec(nodes)
    return out


def build_breadcrumbs(tree: list[TocNode]) -> dict[str, list[str]]:
    """page_id -> list of titles from root to this node (inclusive)."""
    crumbs: dict[str, list[str]] = {}

    def rec(ns: list[TocNode], trail: list[str]) -> None:
        for n in ns:
            new_trail = trail + [n.title]
            crumbs[n.id] = new_trail
            rec(n.children, new_trail)

    rec(tree, [])
    return crumbs


def extract_content(page: Page) -> tuple[str, str, str]:
    """Return (title, html, plain_text) for the current page."""
    sel = first_match(page, SELECTORS["content_root"])
    if sel is None:
        # Last resort: take <body>
        sel = "body"

    title = page.title()
    # Strip the trailing "| Rocket Software Documentation" if present
    title = re.sub(r"\s*[|\-–]\s*Rocket Software Documentation\s*$", "", title).strip()

    html = page.locator(sel).first.inner_html()
    soup = BeautifulSoup(html, "lxml")

    # Strip script/style/nav noise
    for tag in soup.find_all(["script", "style", "nav", "form"]):
        tag.decompose()

    # Clean text for the search index
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)

    # Re-serialise cleaned HTML
    cleaned_html = str(soup)

    # Prefer a real <h1> inside content if present
    h1 = soup.find(["h1", "h2"])
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True)

    return title, cleaned_html, text


# ────────────────────────────────────────────────────────────────────
# Main flow
# ────────────────────────────────────────────────────────────────────


def discover_toc(pw: Playwright, lang: str, debug: bool) -> list[TocNode]:
    """Open start page, expand TOC, return parsed node tree."""
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1400, "height": 1000},
    )
    page = ctx.new_page()

    start = page_url(START_PAGE_ID, lang)
    print(f"→ opening {start}", file=sys.stderr)
    page.goto(start, wait_until="domcontentloaded", timeout=30_000)

    try:
        toc_sel = wait_for_toc(page)
    except RuntimeError:
        Path("debug-page.html").write_text(page.content(), encoding="utf-8")
        print("  wrote debug-page.html (TOC not found — inspect to fix SELECTORS['toc_root'])", file=sys.stderr)
        ctx.close()
        browser.close()
        raise
    print(f"  TOC selector: {toc_sel!r}", file=sys.stderr)

    print("→ expanding all TOC nodes…", file=sys.stderr)
    expand_all_toc_nodes(page, toc_sel)

    toc_html = page.locator(toc_sel).first.inner_html()

    if debug:
        Path("debug-toc.html").write_text(toc_html, encoding="utf-8")
        Path("debug-page.html").write_text(page.content(), encoding="utf-8")
        print("  wrote debug-toc.html and debug-page.html", file=sys.stderr)

    tree = parse_toc_from_html(toc_html, lang)
    if not tree:
        raise RuntimeError(
            "TOC HTML was found but no nodes could be parsed. "
            "Inspect debug-toc.html (run with --debug) and adjust "
            "parse_toc_from_html() if needed."
        )

    ctx.close()
    browser.close()
    return tree


def crawl_pages(
    pw: Playwright,
    tree: list[TocNode],
    crumbs: dict[str, list[str]],
    out_dir: Path,
    lang: str,
    delay_s: float,
    resume: bool,
) -> None:
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "state.json"

    if resume and state_path.exists():
        state = json.loads(state_path.read_text())
        done: set[str] = set(state.get("done", []))
        print(f"→ resuming, {len(done)} pages already done", file=sys.stderr)
    else:
        done = set()

    all_nodes = collect_all_ids(tree)
    todo = [n for n in all_nodes if n.id not in done]
    print(f"→ {len(todo)} pages to fetch (of {len(all_nodes)} total)", file=sys.stderr)

    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1400, "height": 1000},
    )
    page = ctx.new_page()

    try:
        for i, node in enumerate(todo, start=1):
            try:
                page.goto(node.url, wait_until="domcontentloaded", timeout=30_000)
                # Wait for content to settle (Zoomin renders client-side)
                try:
                    page.wait_for_load_state("networkidle", timeout=WAIT_NETWORK_MS)
                except PWTimeout:
                    pass

                title, html, text = extract_content(page)
                payload = {
                    "id": node.id,
                    "title": title or node.title,
                    "breadcrumbs": crumbs.get(node.id, [node.title]),
                    "url": node.url,
                    "html": html,
                    "text": text,
                }
                (pages_dir / f"{node.id}.json").write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8",
                )
                done.add(node.id)

                if i % 5 == 0 or i == len(todo):
                    state_path.write_text(
                        json.dumps({"done": sorted(done)}, ensure_ascii=False)
                    )

                print(
                    f"  [{i}/{len(todo)}] {node.id}  {title[:60]}",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"  ! {node.id} failed: {e}", file=sys.stderr)

            time.sleep(delay_s)
    finally:
        state_path.write_text(
            json.dumps({"done": sorted(done)}, ensure_ascii=False)
        )
        ctx.close()
        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default="de-DE", help="Doc language code (de-DE, en, fr-FR, …). Default: de-DE")
    ap.add_argument("--out", default="data", help="Output directory. Default: data/")
    ap.add_argument("--delay", type=float, default=0.6, help="Seconds between page fetches. Default: 0.6")
    ap.add_argument("--discover", action="store_true", help="Only build the TOC and stop")
    ap.add_argument("--resume", action="store_true", help="Skip pages already scraped")
    ap.add_argument("--debug", action="store_true", help="Dump rendered HTML for inspection")
    args = ap.parse_args()

    # Normalise language: "en" → no prefix, others → keep as-is
    lang = args.lang

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    toc_path = out_dir / "toc.json"

    # On --resume, reuse an existing toc.json to skip the slow re-discovery step.
    if args.resume and toc_path.exists() and not args.discover:
        tree = load_toc_from_json(toc_path)
        flat = collect_all_ids(tree)
        print(f"→ reusing existing TOC ({len(flat)} pages) from {toc_path}", file=sys.stderr)
        crumbs = build_breadcrumbs(tree)
        with sync_playwright() as pw:
            crawl_pages(pw, tree, crumbs, out_dir, lang, args.delay, args.resume)
    else:
        with sync_playwright() as pw:
            tree = discover_toc(pw, lang, args.debug)

            crumbs = build_breadcrumbs(tree)
            toc_path.write_text(
                json.dumps([n.to_dict() for n in tree], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            flat = collect_all_ids(tree)
            print(f"✓ TOC: {len(flat)} pages discovered, written to {toc_path}", file=sys.stderr)

            if args.discover:
                return 0

            crawl_pages(pw, tree, crumbs, out_dir, lang, args.delay, args.resume)

    print("✓ done", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
