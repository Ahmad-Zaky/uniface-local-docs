# Uniface 10.4 Docs — MCP Server & Client

This guide covers everything needed to expose the scraped Uniface 10.4
documentation as an **MCP (Model Context Protocol) server** and interact
with it through an LLM-powered client.

The MCP layer sits **alongside** the browser SPA — both use the same
scraped data and neither breaks the other.

---

## Table of Contents

1. [What is the MCP layer?](#1-what-is-the-mcp-layer)
2. [Project structure](#2-project-structure)
3. [Prerequisites](#3-prerequisites)
4. [Step 1 — Build the MCP data](#4-step-1--build-the-mcp-data)
5. [Step 2 — Install dependencies](#5-step-2--install-dependencies)
6. [Step 3 — Set your API key](#6-step-3--set-your-api-key)
7. [Step 4 — Run the client](#7-step-4--run-the-client)
8. [Step 5 — Wire into Claude Code (optional)](#8-step-5--wire-into-claude-code-optional)
9. [Available tools](#9-available-tools)
10. [Example interactions](#10-example-interactions)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. What is the MCP layer?

**MCP (Model Context Protocol)** is an open standard that lets LLMs call
tools on a running server. Instead of pasting documentation into a prompt,
Claude (or any MCP-compatible model) can call tools like `search_docs` or
`get_page` at inference time and retrieve exactly what it needs.

```
You ──► mcp_client/client.py  ──► Groq LLM (llama-3.3-70b)
                │                        │
                │   tool call request    │
                ▼                        ▼
         mcp_server/server.py ◄──────────┘
                │
         site/assets/pages/
         site/assets/index/
         site/assets/search-meta.json
         site/assets/toc.json
```

The MCP server exposes **6 tools** over stdio. The client wraps an LLM
that decides which tools to call, executes them, and synthesises the
results into a natural-language answer.

**The browser SPA is unaffected** — `docs.json`, `toc.json`, and
`search-meta.json` are not touched.

---

## 2. Project structure

The MCP client is shared across all servers and lives one level above
`DOCUMENTATION/` at the `UNIFACE_MCP` root.

```
UNIFACE_MCP/
│
├── .env                             # ← API keys (copy from .env.example)
├── .env.example                     #   template — fill in and rename to .env
│
├── mcp_client/                      # ← shared client for ALL MCP servers
│   ├── client.py                    #   generic, takes --server PATH
│   ├── requirements.txt
│   └── examples/
│       ├── uniface-docs.txt         #   example prompts for this server
│       └── db-schema.txt            #   example prompts for the schema server
│
├── DOCUMENTATION/
│   ├── scraper/
│   │   ├── scrape.py                #   Playwright scraper (run first)
│   │   ├── build_site_data.py       #   builds SPA assets (docs.json etc.)
│   │   ├── build_mcp_data.py        #   builds MCP assets
│   │   └── requirements.txt
│   │
│   ├── site/
│   │   └── assets/
│   │       ├── toc.json
│   │       ├── docs.json
│   │       ├── search-meta.json
│   │       ├── pages/               #   one JSON per page, text-only
│   │       └── index/               #   precomputed lookup indexes
│   │
│   ├── mcp_server/
│   │   ├── server.py                #   FastMCP server (6 tools)
│   │   └── requirements.txt
│   │
│   ├── README.md
│   └── MCP.md                       #   this file
│
└── DATABASE_SCHEMA/
    ├── mcp_server/
    │   ├── server.py                #   FastMCP server (9 tools)
    │   └── requirements.txt
    └── ...                          #   see DATABASE_SCHEMA_MCP.md
```

---

## 3. Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | The project uses `match`-free syntax; 3.10 is safe |
| Scraped data | `scraper/data/pages/*.json` must exist (run `scrape.py` first — see main README) |
| Virtual environment | Recommended — instructions below use the project `.venv` |

> **If you haven't scraped yet**, follow the main `README.md` first.
> The MCP layer has nothing to serve without the scraped pages.

---

## 4. Step 1 — Build the MCP data

This is a one-time step (re-run only after a fresh scrape).

```bash
cd DOCUMENTATION/scraper
source ../../.venv/bin/activate

python build_mcp_data.py
```

Expected output:

```
→ processing 4990 pages…
  500/4990…
  …
✓ 4990 pages  →  ../site/assets/pages/
✓ 27 sections  →  ../site/assets/index/sections.json
✓ title lookup (4553 entries)  →  ../site/assets/index/title-lookup.json
✓ breadcrumb map  →  ../site/assets/index/breadcrumb-map.json
```

If your scraped data lives somewhere other than `data/`:

```bash
python build_mcp_data.py --data /path/to/scraped/data --out ../site/assets
```

### What this builds

| File | Size | Purpose |
|---|---|---|
| `site/assets/pages/<id>.json` | ~6 KB each | Clean text content per page (no HTML) |
| `site/assets/index/sections.json` | ~1.4 MB | Pages grouped by top-level section |
| `site/assets/index/title-lookup.json` | ~197 KB | Lowercase title → page ID |
| `site/assets/index/breadcrumb-map.json` | ~643 KB | Page ID → breadcrumb string |

---

## 5. Step 2 — Install dependencies

There are two separate venvs — one for the MCP server and one for the
standalone client.

### MCP server venv (required for Claude Code / OpenCode integration)

```bash
cd DOCUMENTATION
python3 -m venv .venv
source .venv/bin/activate
pip install -r mcp_server/requirements.txt
```

`mcp_server/requirements.txt` contains only `mcp>=1.0`.

Verify:

```bash
python3 -c "import mcp; print('OK')"
# → OK
```

### Client venv (required for the standalone `mcp_client`)

Navigate back to the `UNIFACE_MCP` root first (step 1 ran from
`DOCUMENTATION/scraper/`):

```bash
cd ../..   # UNIFACE_MCP/ root
python3 -m venv .venv
source .venv/bin/activate
pip install -r mcp_client/requirements.txt
```

`mcp_client/requirements.txt` covers `mcp`, `python-dotenv`, `groq`,
`anthropic`, `google-genai`, `openai`.

If the root venv already exists:

```bash
source .venv/bin/activate   # from UNIFACE_MCP/ root
```

---

## 6. Step 3 — Set your API key

The client auto-detects which provider to use based on which key is set.

| Provider | Where to get a key | Environment variable | Free? |
|---|---|---|---|
| **Groq** (recommended) | https://console.groq.com → API Keys | `GROQ_API_KEY` | Yes |
| **Gemini** | https://aistudio.google.com/app/apikey | `GEMINI_API_KEY` | Yes |
| **GitHub Models** | https://github.com/settings/personal-access-tokens | `GITHUB_PAT` | Yes (free tier) |
| **Claude** | https://console.anthropic.com | `ANTHROPIC_API_KEY` | No (cheap) |
| **OpenAI** | https://platform.openai.com/api-keys | `OPENAI_API_KEY` | No |

### Recommended — `.env` file

The project ships a `.env.example` template at the `UNIFACE_MCP` root.
Copy it and fill in your key:

```bash
cd UNIFACE_MCP
cp .env.example .env
```

Then open `.env` and set your key (leaving the others commented out):

```bash
# .env — pick ONE
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx

# or GitHub Models (free, no credit card):
# GITHUB_PAT=github_pat_xxxxxxxxxxxxxxxxxxxx
# GITHUB_MODEL=gpt-4o          # optional — default is gpt-4o
```

The client loads `.env` from the `UNIFACE_MCP` root automatically on
startup, regardless of which directory you run from. The file is listed
in `.gitignore` so your keys never end up in version control.

### Alternative — shell export

For one-off sessions or CI environments, export the variable directly:

```bash
export GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
```

> **Groq free tier**: ~14,400 requests/day, 500K tokens/minute on
> `llama-3.3-70b-versatile`. More than enough for documentation queries.
>
> **Gemini free tier**: 1,500 requests/day on `gemini-2.0-flash`.
>
> **GitHub Models free tier**: Rate limits vary by model. Check the model's
> details page at https://github.com/marketplace/models. Uses your existing
> GitHub account; no separate sign-up or credit card required.

---

## 7. Step 4 — Run the client

Run the shared client from the `UNIFACE_MCP` root. It automatically
starts the MCP server as a subprocess — you don't need to launch the
server separately.

```bash
cd UNIFACE_MCP
python mcp_client/client.py \
  --server DOCUMENTATION/mcp_server/server.py \
  --system-prompt "You are a Uniface 10.4 documentation assistant. Always use the available tools to look up information before answering — do not rely on prior knowledge. Cite the page title and URL when referencing documentation." \
  --examples mcp_client/examples/uniface-docs.txt
```

Use `--provider` to pick a backend explicitly, or omit it to
auto-detect from whichever API key is set.

```
Connecting to MCP server  [Groq / llama-3.3-70b-versatile]  (DOCUMENTATION)…
Ready — 6 tools: search_docs, get_page, list_sections, browse_section, lookup_reference, get_toc_children

MCP Assistant  [Groq / llama-3.3-70b-versatile]  server: DOCUMENTATION
Commands: 'examples' · 'quit'  |  Ctrl-C to exit

You: ▌
```

Type any question in natural language. The client prints each tool call
as it happens so you can follow exactly what the model is looking up.

#### Built-in commands

| Input | Action |
|---|---|
| Any question | Run the agentic tool-calling loop and print the answer |
| `examples` | Print the loaded example prompts |
| `1`–`8` | Run example prompt by number |
| `quit` / `exit` / `q` | Exit |
| `Ctrl-C` | Exit |

---

### Demo mode (runs all 8 example prompts)

```bash
cd UNIFACE_MCP
python mcp_client/client.py \
  --server DOCUMENTATION/mcp_server/server.py \
  --examples mcp_client/examples/uniface-docs.txt \
  --demo
```

Good for a first run to see the system working end-to-end.

---

### Single-question mode

```bash
cd UNIFACE_MCP
python mcp_client/client.py --server DOCUMENTATION/mcp_server/server.py --prompt "What is a ProcScript trigger?"
python mcp_client/client.py --server DOCUMENTATION/mcp_server/server.py --provider claude --prompt "Explain entity in Uniface."
```

Prints the answer and exits. Useful for scripting or quick lookups.

---

### Example session

```
You: What does trigger clear do in Uniface?

════════════════════════════════════════════════════════════════
  What does trigger clear do in Uniface?
════════════════════════════════════════════════════════════════

  ⚙  lookup_reference(name='trigger clear')
     ↩  Title: trigger clear | Path: Uniface Reference › Script Module Reference › Triggers…

The **trigger clear** in Uniface is an interactive trigger that reacts to
the user's request to start over with a clean form.

**Declaration:** `trigger clear`
**Applies to:** Form
**Activation:** Activated by the `^CLEAR` structure editor function.

**Default behavior:** None
**Behavior upon completion:** None

**Description:** The default ProcScript provided for this trigger drops all
data currently in the component — any data entered or retrieved is removed
from the component. This does not remove the data from the database itself.

Source: https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/aag1665703130023.html
```

---

## 8. Step 5 — Wire into an AI coding assistant (optional)

You can add the MCP server to **Claude Code** or **OpenCode** so that the
assistant can query your Uniface docs during any conversation — no client
script needed.

---

### Claude Code (VS Code / CLI)

Run the following command, replacing the path with the real location of
this project on your machine:

```bash
claude mcp add -s user uniface-docs \
  /absolute/path/to/UNIFACE_MCP/DOCUMENTATION/.venv/bin/python \
  /absolute/path/to/UNIFACE_MCP/DOCUMENTATION/mcp_server/server.py
```

**Sample — if the project lives at `/home/user/UNIFACE_MCP`:**

```bash
claude mcp add -s user uniface-docs \
  /home/user/UNIFACE_MCP/DOCUMENTATION/.venv/bin/python \
  /home/user/UNIFACE_MCP/DOCUMENTATION/mcp_server/server.py
```

The `-s user` flag registers the server at user scope so it is available
in all your projects. Claude Code saves the entry to `~/.claude.json` in
your home directory — you do not need to edit that file manually.

Verify the server registered and connected:

```bash
claude mcp list
# Expected output:
# uniface-docs: /path/to/.venv/bin/python ... - ✓ Connected
```

---

### OpenCode

Run the interactive wizard:

```bash
opencode mcp add
```

Follow the prompts:

| Prompt | What to enter |
|---|---|
| **Location** | Select **Global** (makes the server available in all projects) |
| **Enter MCP server name** | `uniface-docs` |
| **Type** | Select **local** (stdio-based server) |
| **Command** | `/absolute/path/to/UNIFACE_MCP/DOCUMENTATION/.venv/bin/python /absolute/path/to/UNIFACE_MCP/DOCUMENTATION/mcp_server/server.py` |

OpenCode saves the entry to `~/.config/opencode/opencode.jsonc` — you do
not need to edit that file manually.

Verify the server connected:

```bash
opencode mcp list
# Expected output:
# ● ✓ uniface-docs  connected
#       /path/to/.venv/bin/python /path/to/mcp_server/server.py
```

---

### Verify the server starts correctly (both tools)

```bash
# From DOCUMENTATION/ — should exit cleanly (server waits for stdio input)
source .venv/bin/activate
timeout 2 python mcp_server/server.py 2>/dev/null; echo "server loaded OK"
```

---

## 9. Available tools

| Tool | Arguments | Description |
|---|---|---|
| `search_docs` | `query: str`, `limit: int = 10` | Ranked keyword search across all 4,990 pages. Scores by title (×10), breadcrumb path (×3), body text (×1). Returns up to `limit` results (max 50). |
| `get_page` | `page_id: str` | Full cleaned text of a single page. Use IDs returned by other tools. |
| `list_sections` | — | All 27 top-level sections with page counts. |
| `browse_section` | `section_name: str`, `offset: int = 0`, `limit: int = 30` | Paginated listing of pages within a section. Supports `offset` for large sections. |
| `lookup_reference` | `name: str` | Exact (case-insensitive) title match. Falls back to partial matches and lists candidates. Best for the 2,466 Uniface Reference pages. |
| `get_toc_children` | `page_id: str` | Direct children of a page in the TOC hierarchy. `⊕` means the child has further children; `·` means it is a leaf. |

### Tool decision guide

```
Looking for a named construct?       →  lookup_reference("trigger clear")
Exploring a topic area?              →  search_docs("database connection oracle")
Browsing what's in a section?        →  browse_section("DBMS Support")
Navigating the doc tree?             →  get_toc_children("<parent-id>")
Reading a full page?                 →  get_page("<page-id>")
Seeing what sections exist?          →  list_sections()
```

---

## 10. Example interactions

These are the 8 built-in demo prompts (run with `--demo` or type
`examples` in interactive mode):

```
1. List all the documentation sections and how many pages each has.

2. What does 'trigger clear' do in Uniface? Give me the full details.

3. How do I develop web applications with Uniface? Search for relevant pages.

4. What is a Derived Component Field?

5. Show me the top-level structure of the Uniface documentation tree.

6. I want to connect Uniface to an Oracle database. What should I read?

7. Look up the glossary entry for 'entity' in Uniface.

8. What ProcScript statements are available for working with files?
```

Additional prompts worth trying:

```
- "What triggers are available in a Uniface form?"
- "Explain the difference between a component and an entity in Uniface."
- "How does Uniface handle session management in web apps?"
- "What does the /e qualifier do on the clear statement?"
- "Browse the 'Installing Uniface' section and summarise what's covered."
- "What DBMS systems does Uniface 10.4 support?"
```

---

## 11. Troubleshooting

### `ModuleNotFoundError: No module named 'mcp'`

The `mcp` package is not installed in the active environment.

```bash
source .venv/bin/activate
pip install mcp python-dotenv groq
```

### `ERROR: GROQ_API_KEY is not set`

Either set it in your `.env` file (recommended):

```bash
# .env
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
```

Or export it for the current shell session:

```bash
export GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
```

If you have a `.env` file but the error persists, make sure `python-dotenv`
is installed (`pip install python-dotenv`) and that the key is not commented
out in the file.

### `ERROR: GITHUB_PAT is not set`

Create a Personal Access Token at https://github.com/settings/personal-access-tokens
(no special scopes required — a basic token works). Then:

```bash
# .env
GITHUB_PAT=github_pat_xxxxxxxxxxxxxxxxxxxx
```

### `Page '...' not found` from `get_page`

The `site/assets/pages/` directory is missing or incomplete.
Re-run the data builder:

```bash
cd scraper
python build_mcp_data.py
```

### `Section index not loaded` from `list_sections` or `browse_section`

Same cause — `site/assets/index/` is missing. Re-run `build_mcp_data.py`.

### Search returns zero results

- Check that `site/assets/search-meta.json` exists (built by `build_site_data.py`).
- Try shorter or simpler keywords — the search is keyword-based, not semantic.
- The model may refine the query automatically on a retry; ask again with different phrasing.

### Server fails to start when wired into Claude Code

Make sure the `command` path points to the **server venv Python**, not the
system Python or the root client venv:

```bash
# Find the correct path (run from DOCUMENTATION/)
source .venv/bin/activate
which python
# → /home/user/UNIFACE_MCP/DOCUMENTATION/.venv/bin/python
```

Use that full path in the `claude mcp add` command.

### Rate limit errors (`429`)

Free tiers have per-minute limits. Wait 60 seconds and retry, or switch
to the lighter model via an env var:

```bash
# Groq — switch to the faster small model
export GROQ_MODEL=llama-3.1-8b-instant

# Gemini — already using the default model (gemini-2.0-flash)
# Claude — switch to Haiku (cheapest)
export CLAUDE_MODEL=claude-haiku-4-5-20251001

# OpenAI — switch to a cheaper model
export OPENAI_MODEL=gpt-4o-mini

# GitHub Models — switch to a lighter model
export GITHUB_MODEL=gpt-4o-mini
```

### Using an OpenAI-compatible endpoint (Ollama, Together, etc.)

```bash
export OPENAI_API_KEY=unused          # some endpoints accept any string
export OPENAI_BASE_URL=http://localhost:11434/v1   # e.g. Ollama
export OPENAI_MODEL=llama3.2
python mcp_client/client.py --provider openai
```

### Auto-detect picked the wrong provider

If you have multiple keys set, the client picks the first one found
(Groq → Claude → Gemini → OpenAI → GitHub). Override with `--provider`.

---

## Updating after a fresh scrape

When you re-scrape the documentation, rebuild both the SPA assets and the
MCP data:

```bash
cd scraper
python build_site_data.py --in data --out ../site/assets   # SPA
python build_mcp_data.py                                    # MCP
```

The MCP server picks up the new files automatically on next startup — no
code changes needed.
