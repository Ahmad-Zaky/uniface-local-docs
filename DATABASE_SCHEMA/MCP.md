# Database Schema — MCP Server

This guide covers everything needed to expose your Oracle and MSSQL database
schemas as an **MCP (Model Context Protocol) server** and query them through
any MCP-compatible AI assistant.

The server is **tenant-aware**: it holds multiple tenants (clients), each with
one or more environments (`stage`, `prod`, etc.) and dated snapshots so you
can track schema evolution over time.

---

## Table of Contents

1. [What is the schema MCP layer?](#1-what-is-the-schema-mcp-layer)
2. [Project structure](#2-project-structure)
3. [Prerequisites](#3-prerequisites)
4. [Step 1 — Dump a schema](#4-step-1--dump-a-schema)
5. [Step 2 — Build the MCP assets](#5-step-2--build-the-mcp-assets)
6. [Step 3 — Install dependencies](#6-step-3--install-dependencies)
7. [Step 4 — Run the client](#7-step-4--run-the-client)
8. [Step 5 — Wire into Claude Code or OpenCode](#8-step-5--wire-into-claude-code-or-opencode)
9. [Available tools](#9-available-tools)
10. [Example interactions](#10-example-interactions)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. What is the schema MCP layer?

Instead of pasting a 2 MB SQL file into a prompt, an LLM can call structured
tools at inference time — searching by table name, retrieving a single table's
columns, or diffing two tenants side-by-side.

```
You ──► mcp_client/client.py  ──► Any LLM backend
              │                         │
              │   tool call request     │
              ▼                         ▼
    DATABASE_SCHEMA/
    mcp_server/server.py ◄──────────────┘
              │
    assets/<tenant>/[env/]<date>/
      tables.json
      views.json
      sequences.json
      search-meta.json
    assets/registry.json
```

The MCP server exposes **9 tools** over stdio. These cover per-tenant lookup,
full-text search across column names, and cross-tenant / cross-snapshot
comparison — all without an LLM ever seeing a raw SQL file.

---

## 2. Project structure

The MCP client is shared across all servers and lives at the `UNIFACE_MCP`
root, one level above this directory.

```
UNIFACE_MCP/
│
├── mcp_client/                      # ← shared client for ALL MCP servers
│   ├── client.py                    #   generic, takes --server PATH
│   ├── requirements.txt
│   └── examples/
│       ├── uniface-docs.txt         #   prompts for the docs server
│       └── db-schema.txt            #   prompts for this server
│
└── DATABASE_SCHEMA/
    ├── tenants/                     # SQL dump files live here
    │   ├── xxx/                     # tenant "xxx" (Oracle, no env = default)
    │   │   ├── 2026-05-24_schema.sql
    │   │   └── 2026-05-25_schema.sql
    │   └── mex/                     # tenant "mex"
    │       └── stage/               # environment subfolder
    │           ├── 2026-05-24_schema.sql
    │           └── 2026-05-25_schema.sql
    │
    ├── assets/                      # ← built by build_schema_data.py
    │   ├── registry.json            #   manifest of all tenants/envs/dates
    │   ├── xxx/
    │   │   └── 2026-05-25/
    │   │       ├── tables.json
    │   │       ├── views.json
    │   │       ├── sequences.json
    │   │       └── search-meta.json
    │   └── mex/stage/2026-05-25/
    │       └── ...
    │
    ├── build_schema_data.py         # ← parses SQL dumps → JSON assets
    ├── schema_dump.sh               # ← dumps Oracle / MSSQL schema to file
    ├── mcp_server/
    │   ├── server.py                # ← FastMCP server (9 tools)
    │   └── requirements.txt
    │
    └── README.md                    # schema_dump.sh usage guide
```

The naming convention for dump files is `YYYY-MM-DD_schema.sql`. New dumps
are placed in `tenants/<tenant>/[env/]` and the build script picks them up
automatically on the next run.

---

## 3. Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | Standard library only for the build script; `mcp` for the server |
| At least one SQL dump | See Step 1 below |
| Virtual environment | Recommended — examples below assume the shared `.venv` at the `UNIFACE_MCP` root |

> **Supported dialects**: Oracle (schema-qualified `"SCHEMA"."TABLE"` syntax)
> and Microsoft SQL Server (`[dbo].[table]` syntax). The build script
> auto-detects the dialect from the file content.

---

## 4. Step 1 — Dump a schema

Use `schema_dump.sh` to pull a live schema from Oracle or MSSQL. See
`README.md` in this directory for the full flag reference.

### Oracle

```bash
./schema_dump.sh oracle \
  --host db.example.com \
  --sid ORCL \
  --username scott \
  --password tiger \
  --tenant xxx
# Output: tenants/xxx/YYYY-MM-DD_schema.sql
```

### MSSQL (with environment)

```bash
./schema_dump.sh mssql \
  --host db.example.com \
  --database MyDB \
  --username sa \
  --password secret \
  --tenant mex \
  --env stage
# Output: tenants/mex/stage/YYYY-MM-DD_schema.sql
```

> **Adding a tenant manually**: place any `.sql` file named
> `YYYY-MM-DD_schema.sql` under `tenants/<tenant>/[env/]` and the build
> script handles the rest. Both Oracle and MSSQL dump formats are supported
> in the same `tenants/` tree at the same time.

---

## 5. Step 2 — Build the MCP assets

This parses every SQL dump and writes structured JSON indexes. Run it once
after a new dump arrives; already-built snapshots are skipped by default.

```bash
cd DATABASE_SCHEMA
python3 build_schema_data.py
```

Expected output:

```
Found 4 snapshot(s):

  mex/stage/2026-05-24  (2054 KB)
  → 1884 tables, 302 views, 1 sequences  [mssql]
  mex/stage/2026-05-25  (2110 KB)
  → 1884 tables, 302 views, 1 sequences  [mssql]
  xxx/2026-05-24  (2387 KB)
  → 2244 tables, 434 views, 4 sequences  [oracle]
  xxx/2026-05-25  (2387 KB)
  → 2244 tables, 434 views, 4 sequences  [oracle]

Registry written → .../DATABASE_SCHEMA/assets/registry.json
```

Use `--force` to rebuild everything even if assets already exist:

```bash
python3 build_schema_data.py --force
```

### What this builds

| File | Purpose |
|---|---|
| `assets/<tenant>/[env/]<date>/tables.json` | All tables with columns (name, type, nullable), PK, indexes, FKs |
| `assets/<tenant>/[env/]<date>/views.json` | View names with column lists |
| `assets/<tenant>/[env/]<date>/sequences.json` | Sequence definitions |
| `assets/<tenant>/[env/]<date>/search-meta.json` | Flat list of all table/view names + column names for keyword search |
| `assets/registry.json` | Manifest: every tenant, env, dialect, snapshot dates, and latest date |

---

## 6. Step 3 — Install dependencies

The MCP server requires `mcp`. The client additionally needs `python-dotenv`
and the SDK for your chosen LLM provider.

Create the shared venv once at the `UNIFACE_MCP` root:

```bash
cd ..   # UNIFACE_MCP/ root (skip if already there)
python3 -m venv .venv
source .venv/bin/activate

pip install -r mcp_client/requirements.txt
```

`mcp_client/requirements.txt` covers everything: `mcp`, `python-dotenv`,
`groq`, `anthropic`, `google-genai`, `openai`.

If the venv already exists, activate it from `DATABASE_SCHEMA/`:

```bash
source ../.venv/bin/activate
```

Verify:

```bash
python3 -c "import mcp; print('OK')"
# → OK
```

---

## 7. Step 4 — Run the client

Use the shared client at `UNIFACE_MCP/mcp_client/client.py`. Pass
`--server` to point at this server. The client auto-detects your LLM API
key from the environment or a `.env` file — copy `.env.example` at the
`UNIFACE_MCP` root to `.env` and fill in the key for your chosen provider.

```bash
cd ..   # UNIFACE_MCP/ root
source .venv/bin/activate

# Interactive mode:
python3 mcp_client/client.py \
  --server DATABASE_SCHEMA/mcp_server/server.py \
  --examples mcp_client/examples/db-schema.txt

# With explicit provider:
python3 mcp_client/client.py \
  --server DATABASE_SCHEMA/mcp_server/server.py \
  --provider groq

# Demo mode — runs all examples from db-schema.txt:
python3 mcp_client/client.py \
  --server DATABASE_SCHEMA/mcp_server/server.py \
  --examples mcp_client/examples/db-schema.txt \
  --demo

# Single question and exit:
python3 mcp_client/client.py \
  --server DATABASE_SCHEMA/mcp_server/server.py \
  --prompt "What changed in the xxx schema since yesterday?"
```

```
Connecting to MCP server  [Groq / llama-3.3-70b-versatile]  (DATABASE_SCHEMA)…
Ready — 9 tools: list_tenants, search_schema, get_table, list_tables, …

MCP Assistant  [Groq / llama-3.3-70b-versatile]  server: DATABASE_SCHEMA
Commands: 'examples' · 'quit'  |  Ctrl-C to exit

You: ▌
```

---

## 8. Step 5 — Wire into Claude Code or OpenCode

This is the recommended way to use the schema server. Once registered, Claude
can call schema tools in any conversation — alongside the Uniface docs server
if you have both registered.

### Claude Code (VS Code / CLI)

```bash
claude mcp add -s user uniface-db \
  /absolute/path/to/UNIFACE_MCP/.venv/bin/python3 \
  /absolute/path/to/UNIFACE_MCP/DATABASE_SCHEMA/mcp_server/server.py
```

Verify:

```bash
claude mcp list
# uniface-db: /path/to/python3 ... - ✓ Connected
```

### Running both servers at once

Claude Code merges all registered servers into one tool namespace. You can
register both the docs server and the schema server and Claude will use
whichever tools fit the question:

```bash
# Uniface docs server (register separately — see UNIFACE_MCP root):
# uniface-docs  →  search_docs, get_page, list_sections, …

# Schema server:
claude mcp add -s user uniface-db \
  /absolute/path/to/UNIFACE_MCP/.venv/bin/python3 \
  /absolute/path/to/UNIFACE_MCP/DATABASE_SCHEMA/mcp_server/server.py

claude mcp list
# uniface-docs  - ✓ Connected   (6 tools)
# uniface-db    - ✓ Connected   (9 tools)
```

### OpenCode

```bash
opencode mcp add
```

| Prompt | What to enter |
|---|---|
| **Location** | Select **Global** |
| **Enter MCP server name** | `uniface-db` |
| **Type** | Select **local** |
| **Command** | `/absolute/path/to/UNIFACE_MCP/.venv/bin/python3 /absolute/path/to/UNIFACE_MCP/DATABASE_SCHEMA/mcp_server/server.py` |

---

## 9. Available tools

### Per-tenant tools

| Tool | Arguments | Description |
|---|---|---|
| `list_tenants` | — | All tenants, environments, dialects, latest snapshot date, and full snapshot history. |
| `search_schema` | `query`, `tenant?`, `env?`, `date?`, `limit=15` | Keyword search across table names, view names, and column names. Searches all tenants if `tenant` is omitted. |
| `get_table` | `table_name`, `tenant`, `env?`, `date?` | Full table detail: every column with type and nullability, primary key composition, indexes (name, columns, uniqueness), and foreign keys. |
| `list_tables` | `tenant`, `env?`, `date?`, `prefix?`, `offset=0`, `limit=50` | Paginated table listing with per-table column count, PK size, FK count, and index count. Filter by name prefix. |
| `get_view` | `view_name`, `tenant`, `env?`, `date?` | View name and its column list (where available in the dump). |
| `find_column` | `column_name`, `tenant?`, `env?`, `date?` | Every table that contains a column matching the given name (partial match). Searches all tenants if `tenant` is omitted. |

### Cross-tenant / cross-snapshot tools

| Tool | Arguments | Description |
|---|---|---|
| `compare_table` | `table_name`, `tenant_a`, `tenant_b`, `env_a?`, `env_b?`, `date_a?`, `date_b?` | Column-level diff for one table across two tenants or two snapshots. Reports: columns only in A, only in B, columns in both with type/nullability differences, and identical columns. Warns when dialects differ (Oracle `NUMBER` vs MSSQL `NUMERIC` etc.). |
| `compare_schemas` | `tenant_a`, `tenant_b`, `env_a?`, `env_b?`, `date_a?`, `date_b?`, `show_common?` | High-level overview: tables only in A, only in B, common to both. Flags common tables whose column counts differ. Use `compare_table` to drill in. |
| `diff_snapshots` | `tenant`, `date_a`, `date_b`, `env?` | What changed in one tenant between two dates: new tables, dropped tables, and per-table column additions, removals, and type changes. |

### Tool decision guide

```
What tenants and snapshots do I have?  →  list_tenants()
Find a table by name or column?        →  search_schema("adr_nr")
Read a table's full structure?         →  get_table("AABSLGUE", "xxx")
Browse all tables in a tenant?         →  list_tables("mex", env="stage")
Which tables have column X?            →  find_column("e_mandant")
How does this table differ across
  tenants?                             →  compare_table("AABSLGUE", "xxx", "mex", env_b="stage")
What tables are missing in mex vs
  xxx?                                 →  compare_schemas("xxx", "mex", env_b="stage")
What changed in xxx since yesterday?   →  diff_snapshots("xxx", "2026-05-24", "2026-05-25")
```

### Argument notes

- `env` — pass `"stage"`, `"prod"`, etc. for tenants with an environment
  subfolder. Pass `""` (empty string) or omit for tenants with no environment
  subfolder (the `xxx` tenant in the default setup).
- `date` — always `"YYYY-MM-DD"`. Omit to use the latest available snapshot.
- All table and column names are case-insensitive.

---

## 10. Example interactions

The examples below show the tool calls the LLM makes and the output returned.
Run them interactively in Claude Code or via the standalone client.

---

### List all tenants

```
You: What tenants and database schemas do we have available?

  ⚙  list_tenants()

Available tenants:

  mex/stage
    Dialect:   mssql
    Latest:    2026-05-25
    Snapshots: 2026-05-24, 2026-05-25

  xxx (no env)
    Dialect:   oracle
    Latest:    2026-05-25
    Snapshots: 2026-05-24, 2026-05-25
```

---

### Look up a table

```
You: Show me the full structure of the AABSLGUE table in the xxx tenant.

  ⚙  get_table(table_name='AABSLGUE', tenant='xxx')

Table: AABSLGUE  [xxx/2026-05-25]
────────────────────────────────────────────────────────────
Columns (8):
  ADR_GR                         CHAR(3)              NULL ◆PK
  ADR_NR                         NUMBER(9,0)          NULL ◆PK
  OBJ_NR                         CHAR(3)              NULL ◆PK
  ABSL_NR                        NUMBER(4,0)          NULL ◆PK
  LFD_NR                         NUMBER(2,0)          NULL ◆PK
  AUFT_TYP                       CHAR(1)              NULL
  ANZART_ID                      CHAR(8)              NULL
  E_MANDANT                      CHAR(2)              NULL

Primary Key: ADR_GR, ADR_NR, OBJ_NR, ABSL_NR, LFD_NR

Indexes (1):
  UNIQUE AABSLGUEP1: (ADR_GR, ADR_NR, OBJ_NR, ABSL_NR, LFD_NR)
```

---

### Find all tables that contain a column

```
You: Which tables have an E_MANDANT column in the xxx tenant?

  ⚙  find_column(column_name='E_MANDANT', tenant='xxx')

[xxx/2026-05-25] 875 table(s) with column 'E_MANDANT':

  AABSLGUE: E_MANDANT CHAR(2) NULL
  ABCKLEA: E_MANDANT CHAR(2) NULL
  ABENTBAU: E_MANDANT CHAR(2) NULL
  ABODEF: E_MANDANT CHAR(2) NULL
  ABOFAKT: E_MANDANT CHAR(2) NULL
  …
```

---

### Compare the same table between two tenants

```
You: Is the AABSLGUE table the same in xxx (Oracle) and mex stage (MSSQL)?

  ⚙  compare_table(table_name='AABSLGUE', tenant_a='xxx', tenant_b='mex', env_b='stage')

compare_table: AABSLGUE
  A = [xxx/2026-05-25]  dialect=oracle
  B = [mex/stage/2026-05-25]  dialect=mssql
  ⚠ Cross-dialect comparison: type names differ by convention (NUMBER vs NUMERIC, etc.).

Columns only in A (0):
  (none)

Columns only in B (0):
  (none)

Columns with type/nullability differences (5):
  ABSL_NR
    [xxx/2026-05-25]: NUMBER(4,0) NULL
    [mex/stage/2026-05-25]: NUMERIC(5,0) NOT NULL
  ADR_GR
    [xxx/2026-05-25]: CHAR(3) NULL
    [mex/stage/2026-05-25]: CHAR(3) NOT NULL
  ADR_NR
    [xxx/2026-05-25]: NUMBER(9,0) NULL
    [mex/stage/2026-05-25]: NUMERIC(10,0) NOT NULL
  LFD_NR
    [xxx/2026-05-25]: NUMBER(2,0) NULL
    [mex/stage/2026-05-25]: NUMERIC(3,0) NOT NULL
  OBJ_NR
    [xxx/2026-05-25]: CHAR(3) NULL
    [mex/stage/2026-05-25]: CHAR(3) NOT NULL

Identical columns (3):
  ANZART_ID, AUFT_TYP, E_MANDANT

Primary key A: ['ADR_GR', 'ADR_NR', 'OBJ_NR', 'ABSL_NR', 'LFD_NR']
Primary key B: ['ADR_GR', 'ADR_NR', 'OBJ_NR', 'ABSL_NR', 'LFD_NR']
```

> The 5 differences here are dialect-level: Oracle does not enforce NOT NULL at
> the schema layer in the same way MSSQL does, and `NUMBER` vs `NUMERIC` with
> slightly wider precision are typical Oracle-to-MSSQL migration adjustments.
> The primary keys are identical.

---

### Schema overview: which tables exist in one tenant but not the other?

```
You: Give me an overview of the differences between the xxx and mex schemas.

  ⚙  compare_schemas(tenant_a='xxx', tenant_b='mex', env_b='stage')

compare_schemas
  A = [xxx/2026-05-25]  2244 tables
  B = [mex/stage/2026-05-25]  1884 tables

Tables only in A (415):
  AAA
  ABOFAKT_F_ENDE_DAT
  ABONUMMER
  …

Tables only in B (55):
  ABOWBZ_KUEND
  ARCHIVTRABO
  DWC_T_ABO_STAMM
  …

Tables in both: 1829

Common tables with different column counts (29):
  BLZ                                 A=17 cols  B=18 cols
  CMAUFPOS                            A=66 cols  B=73 cols
  KRANKENH                            A=127 cols  B=132 cols
  PAYVER                              A=24 cols  B=23 cols
  …

Tip: use compare_table('<name>', 'xxx', 'mex') to inspect a specific table.
```

---

### Track what changed between daily snapshots

```
You: What changed in the xxx schema between May 24 and May 25?

  ⚙  diff_snapshots(tenant='xxx', date_a='2026-05-24', date_b='2026-05-25')

diff_snapshots: xxx/(no env)
  Before: xxx/2026-05-24
  After:  xxx/2026-05-25

New tables (0):
  (none)

Dropped tables (0):
  (none)

Column changes in 4 table(s):
  PAYVER:
    + MODIFIED_ON
  PAYVERTRANS:
    + MODIFIED_ON
  UUIDREF:
    + MODIFIED_ON_SYNC
  WBZIMPORT:
    + TERMINFELD_2_RM

Summary: 4 change(s) total.
```

---

### Search for a table or column by keyword

```
You: Search for anything related to "auftrag" in the mex stage schema.

  ⚙  search_schema(query='auftrag', tenant='mex', env='stage')

[mex/stage/2026-05-25] 15 result(s) for 'auftrag':

  [table] CMAUFTRA  (67 cols ◆PK)
    columns: AUFT_NR AUFT_HIST_NR E_MANDANT OBJ_NR AUSG …

  [table] ADAKTION  (7 cols ◆PK)
    columns: ADR_GR ADR_NR AUFT_NR AUFT_HIST_NR DISPO_NR …

  [table] ADBELEG  (11 cols ◆PK)
    columns: ADR_GR ADR_NR AUFT_NR AUFT_HIST_NR …
  …
```

---

### Additional prompts worth trying

```
- "List all tables in the mex stage schema that start with 'ABO'."
- "Does the PAYVER table have the same columns in both tenants?"
- "Find every table in xxx that has a DATUM column."
- "Show me all views in the xxx schema."
- "What sequences are defined in the mex stage schema?"
- "Compare the CMAUFTRA table between xxx and mex — are there structural differences?"
- "How many tables exist only in xxx and not in mex?"
- "What columns were added to the xxx schema in the most recent snapshot?"
```

---

## 11. Troubleshooting

### `No registry found. Run build_schema_data.py first.`

The `assets/` directory has not been built yet. From `DATABASE_SCHEMA/`:

```bash
python3 build_schema_data.py
```

### `Tenant 'xxx' not found. Known tenants: [...]`

Either the tenant name is misspelled or no snapshot has been built for it.
Check `assets/registry.json` for the list of built tenants:

```bash
python3 -c "import json; print(json.load(open('assets/registry.json')))"
```

### `Env '/stage' not found for tenant 'mex'`

The env was misspelled, or the snapshot has not been built. For tenants with
no environment subfolder (like `xxx`), pass `env=""` or omit the argument.

### `Snapshot '2026-05-23' not found`

That date has not been dumped yet, or the dump has not been built. Check the
available snapshots:

```bash
python3 -c "
import json
reg = json.load(open('assets/registry.json'))
for t, envs in reg.items():
    for e, info in envs.items():
        print(t, e or '(no env)', info['snapshots'])
"
```

### `ModuleNotFoundError: No module named 'mcp'`

The `mcp` package is not installed in the active environment:

```bash
source ../.venv/bin/activate
python3 -c "import mcp; print('OK')"
```

### Table shows 0 columns after rebuild

The SQL dump may use a non-standard format. Check which dialect was detected:

```bash
cat assets/<tenant>/[env/]<date>/dialect.txt
```

If it says `unknown`, the parser could not identify Oracle or MSSQL syntax.
Open the dump and check the first `CREATE TABLE` statement — it should match
one of these patterns:

```sql
-- Oracle
  CREATE TABLE "SCHEMA"."TABLENAME"
   (	"COLNAME" TYPE,

-- MSSQL
CREATE TABLE [dbo].[tablename] (
    [colname] TYPE NOT NULL,
```

### Server fails to start when wired into Claude Code

The `command` path must point to the Python binary that has `mcp` installed:

```bash
# Find the correct path (from UNIFACE_MCP root):
source .venv/bin/activate
which python3
# → /path/to/UNIFACE_MCP/.venv/bin/python3
```

Use that full path in the `claude mcp add` command.

### Assets are stale after a new dump

The build script skips already-built snapshots by default. Force a rebuild:

```bash
python3 build_schema_data.py --force
```

Or simply add the new dated file to `tenants/` and run without `--force` —
only the new snapshot will be processed.

---

## Adding a new tenant

1. Dump the schema to `tenants/<new-tenant>/[env/]YYYY-MM-DD_schema.sql`
2. Run `python3 build_schema_data.py`
3. Restart the MCP server (or reconnect in Claude Code)

No code changes are needed. The server reads `registry.json` at startup, so
any tenant that appears there is immediately queryable.
