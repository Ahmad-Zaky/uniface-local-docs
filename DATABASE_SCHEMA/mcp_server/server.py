#!/usr/bin/env python3
"""
Database schema MCP server — tenant-aware, multi-snapshot.

Assets are built by running:
  python ../build_schema_data.py

Tools
-----
  list_tenants          All tenants, envs, dialects, and snapshot dates
  search_schema         Keyword search across table/view/column names
  get_table             Full detail for one table (columns, PK, indexes, FKs)
  list_tables           Paginated table listing for a tenant
  get_view              View detail for one view
  find_column           Which tables contain a given column name
  compare_table         Column-level diff for one table between two tenants/snapshots
  compare_schemas       Overview of tables in tenant A vs B (union / intersection / diff)
  diff_snapshots        What changed in a tenant between two dated snapshots

Wire into Claude Code — add to ~/.claude/claude_desktop_config.json:
  {
    "mcpServers": {
      "uniface-db": {
        "command": "python3",
        "args": ["/absolute/path/to/DATABASE_SCHEMA/mcp_server/server.py"]
      }
    }
  }
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ── Paths ──────────────────────────────────────────────────────────────────

ASSETS_DIR = Path(__file__).parent.parent / "assets"
REGISTRY_PATH = ASSETS_DIR / "registry.json"

# ── Registry ───────────────────────────────────────────────────────────────

def _load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


_REGISTRY: dict = _load_registry()


# ── Asset resolution helpers ───────────────────────────────────────────────

def _resolve_env(tenant: str, env: Optional[str]) -> str:
    """Return the canonical env key (may be empty string for no-env tenants)."""
    if tenant not in _REGISTRY:
        return env or ""
    envs = _REGISTRY[tenant]
    if not envs:
        return ""
    # If env explicitly provided, use it; otherwise pick the only env or ""
    if env:
        return env
    keys = list(envs.keys())
    return keys[0] if len(keys) == 1 else ""


def _resolve_date(tenant: str, env: str, date: Optional[str]) -> Optional[str]:
    if tenant not in _REGISTRY:
        return date
    env_data = _REGISTRY[tenant].get(env, {})
    if not env_data:
        return date
    return date or env_data.get("latest")


def _asset_dir(tenant: str, env: str, date: str) -> Path:
    return ASSETS_DIR / tenant / env / date


def _check_snapshot(tenant: str, env: str, date: str) -> Optional[str]:
    """Return an error string if the snapshot does not exist, else None."""
    if tenant not in _REGISTRY:
        known = list(_REGISTRY.keys())
        return f"Tenant '{tenant}' not found. Known tenants: {known}"
    env_data = _REGISTRY[tenant].get(env, {})
    if not env_data:
        known_envs = list(_REGISTRY[tenant].keys())
        env_label = f"/{env}" if env else " (no env)"
        return f"Env '{env_label}' not found for tenant '{tenant}'. Known envs: {known_envs}"
    if date not in env_data.get("snapshots", []):
        snaps = env_data.get("snapshots", [])
        return f"Snapshot '{date}' not found for {tenant}/{env or '(no env)'}. Available: {snaps}"
    return None


@lru_cache(maxsize=32)
def _load_tables(tenant: str, env: str, date: str) -> dict:
    p = _asset_dir(tenant, env, date) / "tables.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@lru_cache(maxsize=32)
def _load_views(tenant: str, env: str, date: str) -> dict:
    p = _asset_dir(tenant, env, date) / "views.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@lru_cache(maxsize=32)
def _load_search_meta(tenant: str, env: str, date: str) -> list:
    p = _asset_dir(tenant, env, date) / "search-meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _dialect(tenant: str, env: str) -> str:
    return _REGISTRY.get(tenant, {}).get(env, {}).get("dialect", "unknown")


# ── Text helpers ────────────────────────────────────────────────────────────

def _tenant_label(tenant: str, env: str, date: str) -> str:
    return f"{tenant}/{env}/{date}" if env else f"{tenant}/{date}"


def _format_table(tbl: dict, label: str) -> str:
    lines = [f"Table: {tbl['name']}  [{label}]", "─" * 60]

    lines.append(f"Columns ({len(tbl['columns'])}):")
    for col in tbl["columns"]:
        null_flag = "NULL" if col["nullable"] else "NOT NULL"
        pk_flag   = " ◆PK" if col["name"] in tbl.get("primary_key", []) else ""
        lines.append(f"  {col['name']:<30} {col['type']:<20} {null_flag}{pk_flag}")

    if tbl.get("primary_key"):
        lines.append(f"\nPrimary Key: {', '.join(tbl['primary_key'])}")

    if tbl.get("indexes"):
        lines.append(f"\nIndexes ({len(tbl['indexes'])}):")
        for idx in tbl["indexes"]:
            u = "UNIQUE " if idx["unique"] else ""
            lines.append(f"  {u}{idx['name']}: ({', '.join(idx['columns'])})")

    if tbl.get("foreign_keys"):
        lines.append(f"\nForeign Keys ({len(tbl['foreign_keys'])}):")
        for fk in tbl["foreign_keys"]:
            lines.append(
                f"  {fk['name']}: ({', '.join(fk['columns'])})"
                f" → {fk['ref_table']} ({', '.join(fk['ref_columns'])})"
            )

    return "\n".join(lines)


# ── Server ─────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "uniface-db",
    instructions="Database schema assistant — per-tenant, multi-snapshot, cross-tenant comparison",
)


# ── Tool: list_tenants ─────────────────────────────────────────────────────

@mcp.tool()
def list_tenants() -> str:
    """
    List all tenants with their environments, dialects, and available snapshot dates.

    Use the tenant name and env as arguments to other tools.
    An empty env string means the tenant has no environment subfolder (default/prod).
    """
    if not _REGISTRY:
        return "No registry found. Run build_schema_data.py first."

    lines = ["Available tenants:\n"]
    for tenant, envs in sorted(_REGISTRY.items()):
        for env, info in sorted(envs.items()):
            env_label = f"/{env}" if env else " (no env)"
            snaps = info.get("snapshots", [])
            latest = info.get("latest", "—")
            dialect = info.get("dialect", "unknown")
            lines.append(
                f"  {tenant}{env_label}\n"
                f"    Dialect:   {dialect}\n"
                f"    Latest:    {latest}\n"
                f"    Snapshots: {', '.join(snaps)}\n"
            )
    lines.append("Use env='' (empty string) for tenants with no environment subfolder.")
    return "\n".join(lines)


# ── Tool: search_schema ────────────────────────────────────────────────────

@mcp.tool()
def search_schema(
    query: str,
    tenant: Optional[str] = None,
    env: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = 15,
) -> str:
    """
    Keyword search across table and view names and column names.

    Searches all tenants by default; narrow with tenant/env/date.
    Returns up to `limit` results ranked by relevance.

    Args:
        query:  One or more keywords.
        tenant: Tenant name (e.g. 'xxx', 'mex'). Omit to search all.
        env:    Environment (e.g. 'stage'). Use '' for no-env tenants.
        date:   Snapshot date 'YYYY-MM-DD'. Defaults to latest.
        limit:  Max results per tenant (default 15, max 50).
    """
    if not query.strip():
        return "Provide a non-empty query."
    tokens = [t.lower() for t in query.split() if len(t) > 1]
    if not tokens:
        return "Query has no usable terms."
    limit = min(limit, 50)

    # Decide which (tenant, env, date) combos to search
    targets: list[tuple[str, str, str]] = []
    if tenant:
        eff_env  = _resolve_env(tenant, env)
        eff_date = _resolve_date(tenant, eff_env, date)
        if eff_date:
            targets.append((tenant, eff_env, eff_date))
    else:
        for t, envs in _REGISTRY.items():
            for e, info in envs.items():
                latest = info.get("latest")
                if latest:
                    targets.append((t, e, latest))

    if not targets:
        return "No snapshots available."

    all_lines: list[str] = []
    for t, e, d in targets:
        meta = _load_search_meta(t, e, d)
        label = _tenant_label(t, e, d)

        def score(entry: dict) -> float:
            s = 0.0
            name = entry["name"].lower()
            cols = entry.get("columns", "").lower()
            for tok in tokens:
                if tok == name:
                    s += 20
                elif tok in name:
                    s += 10
                if tok in cols:
                    s += 2
            return s

        hits = sorted(((e2, score(e2)) for e2 in meta), key=lambda x: -x[1])
        hits = [(e2, s) for e2, s in hits if s > 0][:limit]

        if not hits:
            all_lines.append(f"[{label}] No results for '{query}'.")
            continue

        all_lines.append(f"[{label}] {len(hits)} result(s) for '{query}':\n")
        for entry, _ in hits:
            typ  = entry["type"]
            name = entry["name"]
            ncol = entry.get("column_count", 0)
            pk   = " ◆PK" if entry.get("has_pk") else ""
            cols_preview = entry.get("columns", "")[:120]
            all_lines.append(
                f"  [{typ}] {name}  ({ncol} cols{pk})\n"
                f"    columns: {cols_preview}{'…' if len(entry.get('columns','')) > 120 else ''}\n"
            )

    return "\n".join(all_lines)


# ── Tool: get_table ────────────────────────────────────────────────────────

@mcp.tool()
def get_table(
    table_name: str,
    tenant: str,
    env: Optional[str] = None,
    date: Optional[str] = None,
) -> str:
    """
    Full detail for one table: columns with types and nullability, primary key,
    indexes, and foreign keys.

    Args:
        table_name: Table name (case-insensitive).
        tenant:     Tenant name (e.g. 'xxx', 'mex').
        env:        Environment (e.g. 'stage'). Use '' for no-env tenants.
        date:       Snapshot date 'YYYY-MM-DD'. Defaults to latest.
    """
    eff_env  = _resolve_env(tenant, env)
    eff_date = _resolve_date(tenant, eff_env, date)
    if not eff_date:
        return f"No snapshot found for tenant '{tenant}'."
    err = _check_snapshot(tenant, eff_env, eff_date)
    if err:
        return err

    tables = _load_tables(tenant, eff_env, eff_date)
    key    = table_name.upper()
    if key not in tables:
        close = [k for k in tables if table_name.upper() in k][:10]
        hint  = f"  Partial matches: {', '.join(close)}" if close else ""
        return f"Table '{table_name}' not found in {tenant}/{eff_env or '(no env)'}.{hint}"

    label = _tenant_label(tenant, eff_env, eff_date)
    return _format_table(tables[key], label)


# ── Tool: list_tables ──────────────────────────────────────────────────────

@mcp.tool()
def list_tables(
    tenant: str,
    env: Optional[str] = None,
    date: Optional[str] = None,
    prefix: Optional[str] = None,
    offset: int = 0,
    limit: int = 50,
) -> str:
    """
    Paginated list of tables for a tenant/snapshot.

    Args:
        tenant: Tenant name.
        env:    Environment. Use '' for no-env tenants.
        date:   Snapshot date. Defaults to latest.
        prefix: Filter to tables whose name starts with this prefix (case-insensitive).
        offset: Skip this many entries (for pagination).
        limit:  Entries per page (default 50, max 200).
    """
    eff_env  = _resolve_env(tenant, env)
    eff_date = _resolve_date(tenant, eff_env, date)
    if not eff_date:
        return f"No snapshot found for tenant '{tenant}'."
    err = _check_snapshot(tenant, eff_env, eff_date)
    if err:
        return err

    tables = _load_tables(tenant, eff_env, eff_date)
    names  = sorted(tables.keys())
    if prefix:
        names = [n for n in names if n.upper().startswith(prefix.upper())]

    limit = min(limit, 200)
    total = len(names)
    chunk = names[offset : offset + limit]
    label = _tenant_label(tenant, eff_env, eff_date)

    lines = [
        f"Tables in [{label}]  "
        f"{'(prefix: ' + prefix + ')  ' if prefix else ''}"
        f"{total} total, showing {offset + 1}–{offset + len(chunk)}\n"
    ]
    for name in chunk:
        tbl  = tables[name]
        ncol = len(tbl["columns"])
        npk  = len(tbl.get("primary_key", []))
        nfk  = len(tbl.get("foreign_keys", []))
        nidx = len(tbl.get("indexes", []))
        lines.append(
            f"  {name:<35} {ncol:>3} cols  "
            f"PK({npk})  FK({nfk})  IDX({nidx})"
        )
    if offset + limit < total:
        lines.append(
            f"\n→ More: list_tables('{tenant}', env='{eff_env}', "
            f"date='{eff_date}', offset={offset + limit})"
        )
    return "\n".join(lines)


# ── Tool: get_view ─────────────────────────────────────────────────────────

@mcp.tool()
def get_view(
    view_name: str,
    tenant: str,
    env: Optional[str] = None,
    date: Optional[str] = None,
) -> str:
    """
    Detail for one view: its column list (where available).

    Args:
        view_name: View name (case-insensitive).
        tenant:    Tenant name.
        env:       Environment. Use '' for no-env tenants.
        date:      Snapshot date. Defaults to latest.
    """
    eff_env  = _resolve_env(tenant, env)
    eff_date = _resolve_date(tenant, eff_env, date)
    if not eff_date:
        return f"No snapshot found for tenant '{tenant}'."
    err = _check_snapshot(tenant, eff_env, eff_date)
    if err:
        return err

    views = _load_views(tenant, eff_env, eff_date)
    key   = view_name.upper()
    if key not in views:
        close = [k for k in views if view_name.upper() in k][:10]
        hint  = f"  Partial matches: {', '.join(close)}" if close else ""
        return f"View '{view_name}' not found in {tenant}/{eff_env or '(no env)'}.{hint}"

    view  = views[key]
    label = _tenant_label(tenant, eff_env, eff_date)
    cols  = view.get("columns", [])
    lines = [
        f"View: {key}  [{label}]",
        "─" * 60,
        f"Columns ({len(cols)}):",
    ]
    for c in cols:
        lines.append(f"  {c}")
    if not cols:
        lines.append("  (column list not available in dump)")
    return "\n".join(lines)


# ── Tool: find_column ──────────────────────────────────────────────────────

@mcp.tool()
def find_column(
    column_name: str,
    tenant: Optional[str] = None,
    env: Optional[str] = None,
    date: Optional[str] = None,
) -> str:
    """
    Find all tables that contain a column with the given name (case-insensitive,
    partial match supported).

    Searches all tenants by default; narrow with tenant/env/date.

    Args:
        column_name: Column name or fragment to search for.
        tenant:      Tenant name. Omit to search all.
        env:         Environment.
        date:        Snapshot date. Defaults to latest.
    """
    if not column_name.strip():
        return "Provide a non-empty column name."

    needle = column_name.upper()

    targets: list[tuple[str, str, str]] = []
    if tenant:
        eff_env  = _resolve_env(tenant, env)
        eff_date = _resolve_date(tenant, eff_env, date)
        if eff_date:
            targets.append((tenant, eff_env, eff_date))
    else:
        for t, envs in _REGISTRY.items():
            for e, info in envs.items():
                latest = info.get("latest")
                if latest:
                    targets.append((t, e, latest))

    if not targets:
        return "No snapshots available."

    all_lines: list[str] = []
    for t, e, d in targets:
        tables = _load_tables(t, e, d)
        label  = _tenant_label(t, e, d)
        hits: list[tuple[str, dict]] = []
        for tname, tbl in sorted(tables.items()):
            matching_cols = [c for c in tbl["columns"] if needle in c["name"]]
            if matching_cols:
                hits.append((tname, matching_cols))

        if not hits:
            all_lines.append(f"[{label}] No tables contain column matching '{column_name}'.")
            continue

        all_lines.append(f"[{label}] {len(hits)} table(s) with column '{column_name}':\n")
        for tname, cols in hits:
            col_strs = ", ".join(
                f"{c['name']} {c['type']} {'NOT NULL' if not c['nullable'] else 'NULL'}"
                for c in cols
            )
            all_lines.append(f"  {tname}: {col_strs}")
        all_lines.append("")

    return "\n".join(all_lines)


# ── Tool: compare_table ────────────────────────────────────────────────────

@mcp.tool()
def compare_table(
    table_name: str,
    tenant_a: str,
    tenant_b: str,
    env_a: Optional[str] = None,
    env_b: Optional[str] = None,
    date_a: Optional[str] = None,
    date_b: Optional[str] = None,
) -> str:
    """
    Column-level diff for one table between two tenants or two snapshots.

    Reports: columns only in A, only in B, in both with type differences,
    and identical columns. Also compares primary key composition.

    Note: Oracle uses NUMBER/VARCHAR2, MSSQL uses NUMERIC/VARCHAR — type
    differences may reflect dialect, not an actual schema difference.

    Args:
        table_name: Table to compare (case-insensitive).
        tenant_a:   First tenant (e.g. 'xxx').
        tenant_b:   Second tenant (e.g. 'mex').
        env_a:      Env for tenant A. Use '' for no-env.
        env_b:      Env for tenant B.
        date_a:     Snapshot date for A. Defaults to latest.
        date_b:     Snapshot date for B. Defaults to latest.
    """
    ea = _resolve_env(tenant_a, env_a)
    eb = _resolve_env(tenant_b, env_b)
    da = _resolve_date(tenant_a, ea, date_a)
    db = _resolve_date(tenant_b, eb, date_b)

    for tenant, env, date in [(tenant_a, ea, da), (tenant_b, eb, db)]:
        if not date:
            return f"No snapshot found for tenant '{tenant}'."
        err = _check_snapshot(tenant, env, date)
        if err:
            return err

    assert da is not None and db is not None
    tables_a = _load_tables(tenant_a, ea, da)
    tables_b = _load_tables(tenant_b, eb, db)

    key = table_name.upper()
    in_a = key in tables_a
    in_b = key in tables_b

    label_a = _tenant_label(tenant_a, ea, da)
    label_b = _tenant_label(tenant_b, eb, db)

    if not in_a and not in_b:
        return f"Table '{table_name}' not found in either snapshot."
    if not in_a:
        return f"Table '{table_name}' exists in [{label_b}] but not in [{label_a}]."
    if not in_b:
        return f"Table '{table_name}' exists in [{label_a}] but not in [{label_b}]."

    tbl_a = tables_a[key]
    tbl_b = tables_b[key]

    cols_a: dict[str, dict] = {c["name"]: c for c in tbl_a["columns"]}
    cols_b: dict[str, dict] = {c["name"]: c for c in tbl_b["columns"]}
    all_cols = sorted(set(cols_a) | set(cols_b))

    only_a:    list[str] = []
    only_b:    list[str] = []
    type_diff: list[str] = []
    identical: list[str] = []

    for col in all_cols:
        if col in cols_a and col not in cols_b:
            c = cols_a[col]
            only_a.append(f"  {col}  {c['type']}  {'NOT NULL' if not c['nullable'] else 'NULL'}")
        elif col in cols_b and col not in cols_a:
            c = cols_b[col]
            only_b.append(f"  {col}  {c['type']}  {'NOT NULL' if not c['nullable'] else 'NULL'}")
        else:
            ca, cb = cols_a[col], cols_b[col]
            if ca["type"] != cb["type"] or ca["nullable"] != cb["nullable"]:
                null_a = "NOT NULL" if not ca["nullable"] else "NULL"
                null_b = "NOT NULL" if not cb["nullable"] else "NULL"
                type_diff.append(
                    f"  {col}\n"
                    f"    [{label_a}]: {ca['type']} {null_a}\n"
                    f"    [{label_b}]: {cb['type']} {null_b}"
                )
            else:
                identical.append(col)

    pk_a = tbl_a.get("primary_key", [])
    pk_b = tbl_b.get("primary_key", [])
    pk_same = pk_a == pk_b

    dialect_a = _dialect(tenant_a, ea)
    dialect_b = _dialect(tenant_b, eb)
    cross_dialect = dialect_a != dialect_b

    lines = [
        f"compare_table: {key}",
        f"  A = [{label_a}]  dialect={dialect_a}",
        f"  B = [{label_b}]  dialect={dialect_b}",
    ]
    if cross_dialect:
        lines.append("  ⚠ Cross-dialect comparison: type names differ by convention (NUMBER vs NUMERIC, etc.).")
    lines.append("")

    lines.append(f"Columns only in A ({len(only_a)}):")
    lines.extend(only_a if only_a else ["  (none)"])

    lines.append(f"\nColumns only in B ({len(only_b)}):")
    lines.extend(only_b if only_b else ["  (none)"])

    lines.append(f"\nColumns with type/nullability differences ({len(type_diff)}):")
    lines.extend(type_diff if type_diff else ["  (none)"])

    lines.append(f"\nIdentical columns ({len(identical)}):")
    lines.append(f"  {', '.join(identical)}" if identical else "  (none)")

    lines.append(f"\nPrimary key A: {pk_a or '(none)'}")
    lines.append(f"Primary key B: {pk_b or '(none)'}")
    if not pk_same:
        lines.append("  ⚠ Primary keys differ.")

    return "\n".join(lines)


# ── Tool: compare_schemas ──────────────────────────────────────────────────

@mcp.tool()
def compare_schemas(
    tenant_a: str,
    tenant_b: str,
    env_a: Optional[str] = None,
    env_b: Optional[str] = None,
    date_a: Optional[str] = None,
    date_b: Optional[str] = None,
    show_common: bool = False,
) -> str:
    """
    High-level overview of tables in tenant A vs tenant B.

    Reports: tables only in A, only in B, tables common to both.
    For the common tables, flags those with column count differences.

    Use compare_table() to drill into a specific table's differences.

    Args:
        tenant_a:     First tenant.
        tenant_b:     Second tenant.
        env_a:        Env for A.
        env_b:        Env for B.
        date_a:       Snapshot date for A. Defaults to latest.
        date_b:       Snapshot date for B. Defaults to latest.
        show_common:  If true, list all common table names (can be long).
    """
    ea = _resolve_env(tenant_a, env_a)
    eb = _resolve_env(tenant_b, env_b)
    da = _resolve_date(tenant_a, ea, date_a)
    db = _resolve_date(tenant_b, eb, date_b)

    for tenant, env, date in [(tenant_a, ea, da), (tenant_b, eb, db)]:
        if not date:
            return f"No snapshot found for tenant '{tenant}'."
        err = _check_snapshot(tenant, env, date)
        if err:
            return err

    assert da is not None and db is not None
    tables_a = _load_tables(tenant_a, ea, da)
    tables_b = _load_tables(tenant_b, eb, db)

    label_a = _tenant_label(tenant_a, ea, da)
    label_b = _tenant_label(tenant_b, eb, db)

    set_a   = set(tables_a)
    set_b   = set(tables_b)
    only_a  = sorted(set_a - set_b)
    only_b  = sorted(set_b - set_a)
    common  = sorted(set_a & set_b)

    # Among common tables, flag those with differing column counts
    col_count_diffs: list[str] = []
    for t in common:
        na = len(tables_a[t]["columns"])
        nb = len(tables_b[t]["columns"])
        if na != nb:
            col_count_diffs.append(f"  {t:<35} A={na} cols  B={nb} cols")

    lines = [
        f"compare_schemas",
        f"  A = [{label_a}]  {len(set_a)} tables",
        f"  B = [{label_b}]  {len(set_b)} tables",
        "",
        f"Tables only in A ({len(only_a)}):",
    ]
    if only_a:
        for t in only_a:
            lines.append(f"  {t}")
    else:
        lines.append("  (none)")

    lines.append(f"\nTables only in B ({len(only_b)}):")
    if only_b:
        for t in only_b:
            lines.append(f"  {t}")
    else:
        lines.append("  (none)")

    lines.append(f"\nTables in both: {len(common)}")

    if col_count_diffs:
        lines.append(f"\nCommon tables with different column counts ({len(col_count_diffs)}):")
        lines.extend(col_count_diffs)
    else:
        lines.append("\nAll common tables have the same column count.")

    if show_common:
        lines.append(f"\nAll common tables:")
        for t in common:
            lines.append(f"  {t}")

    lines.append(
        f"\nTip: use compare_table('<name>', '{tenant_a}', '{tenant_b}') to inspect a specific table."
    )
    return "\n".join(lines)


# ── Tool: diff_snapshots ───────────────────────────────────────────────────

@mcp.tool()
def diff_snapshots(
    tenant: str,
    date_a: str,
    date_b: str,
    env: Optional[str] = None,
) -> str:
    """
    What changed in a tenant between two dated snapshots (same tenant, same env).

    Reports: new tables, dropped tables, and per-table column changes
    (added columns, dropped columns, modified types/nullability).

    Args:
        tenant: Tenant name.
        date_a: Older snapshot date 'YYYY-MM-DD'.
        date_b: Newer snapshot date 'YYYY-MM-DD'.
        env:    Environment. Use '' for no-env tenants.
    """
    eff_env = _resolve_env(tenant, env)

    for date in [date_a, date_b]:
        err = _check_snapshot(tenant, eff_env, date)
        if err:
            return err

    tables_a = _load_tables(tenant, eff_env, date_a)
    tables_b = _load_tables(tenant, eff_env, date_b)

    label_a = _tenant_label(tenant, eff_env, date_a)
    label_b = _tenant_label(tenant, eff_env, date_b)

    set_a = set(tables_a)
    set_b = set(tables_b)

    new_tables     = sorted(set_b - set_a)
    dropped_tables = sorted(set_a - set_b)
    common         = sorted(set_a & set_b)

    col_changes: list[str] = []
    for t in common:
        cols_a: dict[str, dict] = {c["name"]: c for c in tables_a[t]["columns"]}
        cols_b: dict[str, dict] = {c["name"]: c for c in tables_b[t]["columns"]}
        added   = sorted(set(cols_b) - set(cols_a))
        dropped = sorted(set(cols_a) - set(cols_b))
        modified: list[str] = []
        for col in sorted(set(cols_a) & set(cols_b)):
            ca, cb = cols_a[col], cols_b[col]
            if ca["type"] != cb["type"] or ca["nullable"] != cb["nullable"]:
                null_a = "NOT NULL" if not ca["nullable"] else "NULL"
                null_b = "NOT NULL" if not cb["nullable"] else "NULL"
                modified.append(
                    f"    {col}: {ca['type']} {null_a} → {cb['type']} {null_b}"
                )
        if added or dropped or modified:
            parts: list[str] = [f"  {t}:"]
            if added:
                parts.append(f"    + {', '.join(added)}")
            if dropped:
                parts.append(f"    - {', '.join(dropped)}")
            parts.extend(modified)
            col_changes.append("\n".join(parts))

    lines = [
        f"diff_snapshots: {tenant}/{eff_env or '(no env)'}",
        f"  Before: {label_a}",
        f"  After:  {label_b}",
        "",
        f"New tables ({len(new_tables)}):",
    ]
    lines.extend(f"  {t}" for t in new_tables) if new_tables else lines.append("  (none)")

    lines.append(f"\nDropped tables ({len(dropped_tables)}):")
    lines.extend(f"  {t}" for t in dropped_tables) if dropped_tables else lines.append("  (none)")

    if col_changes:
        lines.append(f"\nColumn changes in {len(col_changes)} table(s):")
        lines.extend(col_changes)
    else:
        lines.append("\nNo column changes in common tables.")

    total_changes = len(new_tables) + len(dropped_tables) + len(col_changes)
    lines.append(f"\nSummary: {total_changes} change(s) total.")
    return "\n".join(lines)


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
