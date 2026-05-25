# Uniface 10.4 — Local Docs

A local mirror of the Rocket Uniface 10.4 documentation with **fast,
fully-offline, client-side full-text search**.

```
uniface-docs/
├── scraper/
│   ├── scrape.py              # Playwright scraper (handles JS-rendered TOC)
│   ├── build_site_data.py     # Bundles scraped data for the site
│   └── requirements.txt
├── site/
│   ├── index.html
│   └── assets/
│       ├── style.css
│       ├── app.js
│       └── (toc.json, docs.json, search-meta.json   — created by step 3)
└── README.md
```

## Disclaimer

The Uniface documentation is **copyright Rocket Software**. This tool just
mirrors it locally for your own reference — do **not** redistribute the
scraped content or host the resulting site publicly.

## Setup

Tested on Linux + Python 3.11. Should work on any OS Playwright supports.

```bash
cd scraper

# 1. Python deps
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Playwright's browser binary (one-time, ~150 MB)
python3 -m playwright install chromium
```

## Scrape

The scraper is **two-phase** — discover first, then crawl. This lets you
catch DOM-selector issues before doing a full crawl.

```bash
# Phase A: discovery. Builds toc.json only. ~30 sec.
python3 scrape.py --discover

# Inspect the output
jq '.[0:3]' data/toc.json
wc -l data/toc.json
```

If the TOC looks right (you should see a nested tree with German titles),
do the full crawl:

```bash
# Phase B: crawl every page. Could take 15–40 min depending on page count.
# Polite default: 600 ms between fetches.
python3 scrape.py

# Or, in English instead:
python3 scrape.py --lang en

# Interrupted? Resume — already-scraped pages are skipped.
python3 scrape.py --resume
```

### Troubleshooting

The scraper depends on Zoomin's DOM, which can change. If something fails:

```bash
# Dumps the rendered HTML to debug-toc.html and debug-page.html
python3 scrape.py --discover --debug
```

Open `debug-toc.html` and `debug-page.html` in a browser, find the actual
selectors with devtools, then update the `SELECTORS` dict at the top of
`scrape.py`. The script tries multiple fallback selectors already.

## Build site data

After scraping, bundle everything for the viewer:

```bash
python3 build_site_data.py --in data --out ../site/assets
```

This produces three files in `site/assets/`:

| File | Purpose |
|---|---|
| `toc.json` | Hierarchical sidebar tree |
| `docs.json` | Full HTML content per page (lazy-loaded by id) |
| `search-meta.json` | Plain-text corpus that the search index is built from |

## Run the site

It's a pure static site. Any HTTP server works:

```bash
cd ../site
python3 -m http.server 8000
# → open http://localhost:8000
```

Or even simpler — most browsers will load it from `file://` directly:

```bash
xdg-open index.html        # Linux
open index.html            # macOS
```

(Chrome may block `fetch()` of local JSON via `file://`. If so, use the
HTTP server. Firefox is more permissive.)

## Using the site

- **`/`** — focus search box
- **`↑` `↓`** — navigate results
- **`Enter`** — open selected result
- **`Esc`** — clear search
- Click anywhere in the sidebar to jump to a topic
- URLs deep-link by page id: `…/index.html#etd1665702764995`

The search index is built once on first load (a few seconds) and cached
in `localStorage`. Subsequent loads are instant.

## Updating

When Rocket publishes new patches, just re-run:

```bash
cd scraper
python3 scrape.py --resume   # picks up only new/missing pages
python3 build_site_data.py --in data --out ../site/assets
```

The viewer auto-detects the changed corpus (via fingerprint) and rebuilds
the search index on the next page load.
