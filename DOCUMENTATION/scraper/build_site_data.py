#!/usr/bin/env python3
"""
Build site data from scraped output:

  data/
    toc.json
    pages/<id>.json

→ site/assets/
    toc.json           (copy)
    docs.json          { id: { title, breadcrumbs, html } }   — full content
    search-meta.json   [ { id, title, breadcrumbs, text } ]    — search corpus

The actual Lunr search index is built client-side from search-meta.json on
first load and cached in localStorage. Keeping it client-side lets the site
work as plain static files (file:// or any HTTP server) — no Node build
step needed.

USAGE
    python build_site_data.py --in data --out ../site/assets
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

# Zoomin UI chrome elements — present on every page but useless locally.
# Identified from the zDocs* class names in the scraped HTML.
_NOISE_SELECTORS = [
    # zDocsTopicPageHead is handled separately in clean_html (conditional on content h1)
    "[class*='zDocsTopicActions']",     # toolbar: subscribe, share, export, watch
    "[class*='zDocsBundlePagination']", # prev / next topic navigation
    "[class*='zDocsScrollTopBtn']",     # scroll-to-top button
    "[class*='zDocsMyDocsMenu']",       # "My Topics" menu
    "[class*='zDocsExportPdfMenu']",    # export PDF dropdown
    "[class*='zDocsDropdownMenu']",     # any remaining dropdowns
    "[class*='zDocsFeedback']",         # feedback widget
    "[class*='zDocsAiTopicSummary']",   # AI summary button/panel
    "[class*='zDocsTopicPageDetails']", # "last updated" metadata bar
    "[class*='zDocsTopicActionsMobile']",
    "[class*='zDocsShareDialog']",
    "[data-testid='next-prev-container']",
    "script",
    "style",
    "form",
]

# Pattern that matches internal doc links: /xx-XX/bundle/NAME/page/ID.html
_PAGE_LINK_RE = re.compile(
    r'/(?:[a-z]{2}-[A-Z]{2}/)?bundle/[^/]+/page/([^/?"]+?)\.html'
)


def clean_html(raw_html: str) -> str:
    """Strip Zoomin chrome and rewrite internal links to #id hash navigation."""
    soup = BeautifulSoup(raw_html, "lxml")

    # 1. Remove noise elements
    for sel in _NOISE_SELECTORS:
        for el in soup.select(sel):
            el.decompose()

    # Handle zDocsTopicPageHead: strip it only when the content body already
    # has its own h1/h2 (avoiding a duplicate title). When the head holds the
    # only title, keep it so the page isn't left titleless.
    page_head = soup.select_one("[class*='zDocsTopicPageHead']")
    if page_head:
        body_content = soup.select_one("[class*='zDocsTopicPageBodyContent']")
        # Only h1 counts as a page-level title; h2+ are section headings within the page
        content_has_heading = bool(body_content and body_content.find("h1")) if body_content else False
        if content_has_heading:
            page_head.decompose()
        else:
            # Keep only the h1/h2 from the head; strip everything else inside it
            for child in list(page_head.children):
                if getattr(child, "name", None) not in ("h1", "h2", None):
                    child.decompose()

    # 2. Rewrite internal page links  →  #page-id
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        m = _PAGE_LINK_RE.search(href)
        if m:
            tag["href"] = f"#{m.group(1)}"
        elif href.startswith("/") and "/auth/" in href:
            # Auth/login redirects — remove the link, keep the text
            tag.unwrap()

    # 3. Re-serialise, dropping the lxml-added <html><body> wrapper
    body = soup.find("body")
    return "".join(str(c) for c in body.children) if body else str(soup)


def truncate(text: str, n: int = 4000) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="data", help="Scraped data dir")
    ap.add_argument("--out", dest="dst", default="../site/assets", help="Output dir")
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)

    toc_in = src / "toc.json"
    if not toc_in.exists():
        print(f"missing {toc_in}", file=sys.stderr)
        return 1

    # 1. Copy TOC verbatim
    toc = json.loads(toc_in.read_text(encoding="utf-8"))
    (dst / "toc.json").write_text(json.dumps(toc, ensure_ascii=False), encoding="utf-8")

    # 2. Collect all pages
    pages_dir = src / "pages"
    docs: dict[str, dict] = {}
    search_corpus: list[dict] = []

    page_files = sorted(pages_dir.glob("*.json"))
    for i, fp in enumerate(page_files, 1):
        page = json.loads(fp.read_text(encoding="utf-8"))
        pid = page["id"]
        raw_html = page.get("html", "")
        cleaned = clean_html(raw_html) if raw_html else ""

        # Derive plain text from cleaned HTML for search index
        text_soup = BeautifulSoup(cleaned, "lxml")
        plain = text_soup.get_text(separator=" ", strip=True)
        plain = re.sub(r"\s+", " ", plain)

        docs[pid] = {
            "title": page.get("title", ""),
            "breadcrumbs": page.get("breadcrumbs", []),
            "url": page.get("url", ""),
            "html": cleaned,
        }
        search_corpus.append({
            "id": pid,
            "title": page.get("title", ""),
            "breadcrumbs": " › ".join(page.get("breadcrumbs", [])),
            "text": truncate(plain),
        })

        if i % 200 == 0:
            print(f"  processed {i}/{len(page_files)}…", file=sys.stderr)

    (dst / "docs.json").write_text(
        json.dumps(docs, ensure_ascii=False),
        encoding="utf-8",
    )
    (dst / "search-meta.json").write_text(
        json.dumps(search_corpus, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"✓ {len(docs)} pages → {dst}/docs.json", file=sys.stderr)
    print(f"✓ search corpus → {dst}/search-meta.json", file=sys.stderr)
    print(f"  total search payload: {(dst / 'search-meta.json').stat().st_size / 1024:.1f} KB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
