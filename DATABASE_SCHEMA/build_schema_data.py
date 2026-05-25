#!/usr/bin/env python3
"""
Build MCP schema assets from tenant SQL snapshots.

Reads:  tenants/<tenant>/[env/]<YYYY-MM-DD>_schema.sql
Writes: assets/<tenant>/[env/]<YYYY-MM-DD>/{tables,views,sequences,search-meta}.json
        assets/registry.json

Usage:
  python build_schema_data.py           # skip already-built snapshots
  python build_schema_data.py --force   # rebuild everything
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TENANTS_DIR = Path(__file__).parent / "tenants"
ASSETS_DIR  = Path(__file__).parent / "assets"


# ── Dialect detection ──────────────────────────────────────────────────────

def detect_dialect(text: str) -> str:
    if re.search(r'CREATE\s+TABLE\s+"[^"]+"\."', text, re.IGNORECASE):
        return "oracle"
    if re.search(r'CREATE\s+TABLE\s+\[', text, re.IGNORECASE):
        return "mssql"
    if "VARCHAR2" in text or re.search(r'\bNUMBER\s*\(', text):
        return "oracle"
    if "GO\n" in text or text.startswith("GO\n"):
        return "mssql"
    return "unknown"


# ── Oracle parser ──────────────────────────────────────────────────────────

def _parse_oracle_columns(col_lines: list[str]) -> list[dict]:
    columns: list[dict] = []
    for raw in col_lines:
        line = raw.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        m = re.match(r'"([^"]+)"\s+(.+)', line)
        if not m:
            continue
        name = m.group(1).upper()
        type_str = m.group(2).strip()
        nullable = True
        upper = type_str.upper()
        if upper.endswith(" NOT NULL"):
            nullable = False
            type_str = type_str[:-9].strip()
        elif upper.endswith(" NULL"):
            type_str = type_str[:-5].strip()
        columns.append({"name": name, "type": type_str, "nullable": nullable})
    return columns


def parse_oracle(text: str) -> dict:
    tables: dict[str, dict] = {}
    views:  dict[str, dict] = {}
    sequences: list[dict]   = []

    lines = text.split("\n")
    i, n = 0, len(lines)

    # ── Tables ──────────────────────────────────────────────────────────
    while i < n:
        m = re.match(r'\s*CREATE\s+TABLE\s+"[^"]+"\."([^"]+)"', lines[i], re.IGNORECASE)
        if m:
            table_name = m.group(1).upper()
            col_lines: list[str] = []
            i += 1
            while i < n:
                stripped = lines[i].strip()
                if re.match(r'^\)\s*;?\s*$', stripped):
                    i += 1
                    break
                if stripped.startswith("("):
                    content = stripped[1:].strip()
                    if content and not content.startswith("--"):
                        col_lines.append(content)
                elif stripped and not stripped.startswith("--"):
                    col_lines.append(stripped)
                i += 1
            tables[table_name] = {
                "name": table_name,
                "columns": _parse_oracle_columns(col_lines),
                "primary_key": [],
                "indexes": [],
                "foreign_keys": [],
            }
            continue
        i += 1

    # ── Primary keys ─────────────────────────────────────────────────────
    pk_re = re.compile(
        r'ALTER TABLE\s+"[^"]+"\."([^"]+)"\s+ADD CONSTRAINT\s+"[^"]+"\s+PRIMARY KEY\s*\(([^)]+)\)',
        re.IGNORECASE,
    )
    for m in pk_re.finditer(text):
        tbl = m.group(1).upper()
        cols = [c.strip().strip('"').upper() for c in m.group(2).split(",")]
        if tbl in tables:
            tables[tbl]["primary_key"] = cols

    # ── Indexes ──────────────────────────────────────────────────────────
    idx_re = re.compile(
        r'CREATE\s+(UNIQUE\s+)?INDEX\s+"[^"]+"\."([^"]+)"\s+ON\s+"[^"]+"\."([^"]+)"\s*\(([^)]+)\)',
        re.IGNORECASE,
    )
    for m in idx_re.finditer(text):
        unique     = m.group(1) is not None
        idx_name   = m.group(2).upper()
        tbl        = m.group(3).upper()
        cols       = [c.strip().strip('"').upper() for c in m.group(4).split(",")]
        if tbl in tables:
            tables[tbl]["indexes"].append({"name": idx_name, "columns": cols, "unique": unique})

    # ── Foreign keys ─────────────────────────────────────────────────────
    fk_re = re.compile(
        r'ALTER TABLE\s+"[^"]+"\."([^"]+)"\s+ADD CONSTRAINT\s+"([^"]+)"\s+FOREIGN KEY\s*\(([^)]+)\)'
        r'\s+REFERENCES\s+"[^"]+"\."([^"]+)"\s*\(([^)]+)\)',
        re.IGNORECASE,
    )
    for m in fk_re.finditer(text):
        tbl      = m.group(1).upper()
        fk_name  = m.group(2).upper()
        cols     = [c.strip().strip('"').upper() for c in m.group(3).split(",")]
        ref_tbl  = m.group(4).upper()
        ref_cols = [c.strip().strip('"').upper() for c in m.group(5).split(",")]
        if tbl in tables:
            tables[tbl]["foreign_keys"].append({
                "name": fk_name, "columns": cols,
                "ref_table": ref_tbl, "ref_columns": ref_cols,
            })

    # ── Views ────────────────────────────────────────────────────────────
    view_re = re.compile(
        r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:\w+\s+)*VIEW\s+"[^"]+"\."([^"]+)"\s*\(([^)]+)\)\s+AS',
        re.IGNORECASE,
    )
    for m in view_re.finditer(text):
        vname = m.group(1).upper()
        cols  = [c.strip().strip('"').upper() for c in m.group(2).split(",")]
        views[vname] = {"name": vname, "columns": cols}

    # ── Sequences ────────────────────────────────────────────────────────
    seq_re = re.compile(
        r'CREATE\s+SEQUENCE\s+"[^"]+"\."([^"]+)"\s+.*?(?:INCREMENT BY\s+(\d+))?'
        r'.*?(?:START WITH\s+(\d+))?',
        re.IGNORECASE,
    )
    for m in seq_re.finditer(text):
        sequences.append({
            "name":       m.group(1).upper(),
            "increment":  int(m.group(2)) if m.group(2) else 1,
            "start_with": int(m.group(3)) if m.group(3) else 1,
        })

    return {"tables": tables, "views": views, "sequences": sequences}


# ── MSSQL parser ───────────────────────────────────────────────────────────

def _parse_mssql_columns(col_lines: list[str]) -> list[dict]:
    columns: list[dict] = []
    for raw in col_lines:
        line = raw.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        m = re.match(r'\[([^\]]+)\]\s+(.+)', line)
        if not m:
            continue
        name     = m.group(1).upper()
        type_str = m.group(2).strip()
        nullable = True
        upper    = type_str.upper()
        if upper.endswith(" NOT NULL"):
            nullable = False
            type_str = type_str[:-9].strip()
        elif upper.endswith(" NULL"):
            type_str = type_str[:-5].strip()
        columns.append({"name": name, "type": type_str, "nullable": nullable})
    return columns


def parse_mssql(text: str) -> dict:
    tables:    dict[str, dict] = {}
    views:     dict[str, dict] = {}
    sequences: list[dict]      = []

    lines = text.split("\n")
    i, n = 0, len(lines)

    # ── Tables ──────────────────────────────────────────────────────────
    while i < n:
        m = re.match(
            r'\s*CREATE\s+TABLE\s+(?:\[[^\]]+\]\.)?\[([^\]]+)\]\s*\(',
            lines[i], re.IGNORECASE,
        )
        if m:
            table_name = m.group(1).upper()
            col_lines: list[str] = []
            # Content after the opening "(" on the CREATE TABLE line
            rest = lines[i][m.end():].strip()
            if rest and not rest.startswith("--"):
                col_lines.append(rest)
            i += 1
            while i < n:
                stripped = lines[i].strip()
                if re.match(r'^\)\s*;?\s*$', stripped):
                    i += 1
                    break
                if stripped and not stripped.startswith("--"):
                    col_lines.append(stripped)
                i += 1
            tables[table_name] = {
                "name": table_name,
                "columns": _parse_mssql_columns(col_lines),
                "primary_key": [],
                "indexes": [],
                "foreign_keys": [],
            }
            continue
        i += 1

    # ── Primary keys (PRIMARY_KEY_CONSTRAINT is the dump's custom keyword) ─
    pk_re = re.compile(
        r'ALTER TABLE\s+(?:\[[^\]]+\]\.)?\[([^\]]+)\]\s+ADD CONSTRAINT\s+\[[^\]]+\]'
        r'\s+PRIMARY(?:_KEY)?(?:_CONSTRAINT)?\s+(?:KEY\s*)?\(([^)]+)\)',
        re.IGNORECASE,
    )
    for m in pk_re.finditer(text):
        tbl  = m.group(1).upper()
        cols = [c.strip().strip("[]").upper() for c in m.group(2).split(",")]
        if tbl in tables:
            tables[tbl]["primary_key"] = cols

    # ── Indexes ──────────────────────────────────────────────────────────
    idx_re = re.compile(
        r'CREATE\s+((?:UNIQUE\s+)?(?:NONCLUSTERED\s+|CLUSTERED\s+)?)'
        r'INDEX\s+\[([^\]]+)\]\s+ON\s+(?:\[[^\]]+\]\.)?\[([^\]]+)\]\s*\(([^)]+)\)',
        re.IGNORECASE,
    )
    for m in idx_re.finditer(text):
        qualifier = m.group(1).upper()
        unique    = "UNIQUE" in qualifier
        idx_name  = m.group(2).upper()
        tbl       = m.group(3).upper()
        cols      = [c.strip().strip("[]").upper() for c in m.group(4).split(",")]
        if tbl in tables:
            tables[tbl]["indexes"].append({"name": idx_name, "columns": cols, "unique": unique})

    # ── Foreign keys ─────────────────────────────────────────────────────
    fk_re = re.compile(
        r'ALTER TABLE\s+(?:\[[^\]]+\]\.)?\[([^\]]+)\]\s+ADD CONSTRAINT\s+\[([^\]]+)\]'
        r'\s+FOREIGN KEY\s*\(([^)]+)\)\s+REFERENCES\s+(?:\[[^\]]+\]\.)?\[([^\]]+)\]\s*\(([^)]+)\)',
        re.IGNORECASE,
    )
    for m in fk_re.finditer(text):
        tbl      = m.group(1).upper()
        fk_name  = m.group(2).upper()
        cols     = [c.strip().strip("[]").upper() for c in m.group(3).split(",")]
        ref_tbl  = m.group(4).upper()
        ref_cols = [c.strip().strip("[]").upper() for c in m.group(5).split(",")]
        if tbl in tables:
            tables[tbl]["foreign_keys"].append({
                "name": fk_name, "columns": cols,
                "ref_table": ref_tbl, "ref_columns": ref_cols,
            })

    # ── Views ────────────────────────────────────────────────────────────
    view_re = re.compile(
        r'CREATE\s+VIEW\s+(?:\[[^\]]+\]\.)?\[?([^\]\s(]+)\]?',
        re.IGNORECASE,
    )
    for m in view_re.finditer(text):
        vname = m.group(1).upper().rstrip("]")
        if vname not in views:
            views[vname] = {"name": vname, "columns": []}

    # ── Sequences ────────────────────────────────────────────────────────
    seq_re = re.compile(
        r'CREATE\s+SEQUENCE\s+(?:\[[^\]]+\]\.)?\[([^\]]+)\]'
        r'(?:\s+AS\s+\S+)?(?:\s+START\s+WITH\s+(\d+))?(?:\s+INCREMENT\s+BY\s+(\d+))?',
        re.IGNORECASE,
    )
    for m in seq_re.finditer(text):
        sequences.append({
            "name":       m.group(1).upper(),
            "start_with": int(m.group(2)) if m.group(2) else 1,
            "increment":  int(m.group(3)) if m.group(3) else 1,
        })

    return {"tables": tables, "views": views, "sequences": sequences}


# ── Search index builder ───────────────────────────────────────────────────

def build_search_meta(tables: dict, views: dict) -> list[dict]:
    meta: list[dict] = []
    for name, tbl in tables.items():
        col_names = " ".join(c["name"] for c in tbl["columns"])
        meta.append({
            "name":         name,
            "type":         "table",
            "columns":      col_names,
            "column_count": len(tbl["columns"]),
            "has_pk":       bool(tbl["primary_key"]),
        })
    for name, view in views.items():
        col_names = " ".join(view.get("columns", []))
        meta.append({
            "name":         name,
            "type":         "view",
            "columns":      col_names,
            "column_count": len(view.get("columns", [])),
            "has_pk":       False,
        })
    return sorted(meta, key=lambda x: x["name"])


# ── Asset writer ───────────────────────────────────────────────────────────

def write_assets(out_dir: Path, parsed: dict, dialect: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables    = parsed["tables"]
    views     = parsed["views"]
    sequences = parsed["sequences"]

    (out_dir / "tables.json").write_text(
        json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "views.json").write_text(
        json.dumps(views, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "sequences.json").write_text(
        json.dumps(sequences, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "search-meta.json").write_text(
        json.dumps(build_search_meta(tables, views), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "dialect.txt").write_text(dialect, encoding="utf-8")

    print(
        f"  → {len(tables)} tables, {len(views)} views, {len(sequences)} sequences  [{dialect}]"
    )


# ── Snapshot discovery ─────────────────────────────────────────────────────

def discover_snapshots(tenants_dir: Path) -> list[tuple[str, str, str, Path]]:
    """Return list of (tenant, env, date, sql_path).  env='' means no subfolder."""
    snapshots: list[tuple[str, str, str, Path]] = []
    date_re = re.compile(r'^(\d{4}-\d{2}-\d{2})_schema\.sql$')

    for tenant_dir in sorted(tenants_dir.iterdir()):
        if not tenant_dir.is_dir():
            continue
        tenant = tenant_dir.name

        for entry in sorted(tenant_dir.iterdir()):
            if entry.is_file():
                m = date_re.match(entry.name)
                if m:
                    snapshots.append((tenant, "", m.group(1), entry))
            elif entry.is_dir():
                env = entry.name
                for sql_file in sorted(entry.iterdir()):
                    if sql_file.is_file():
                        m = date_re.match(sql_file.name)
                        if m:
                            snapshots.append((tenant, env, m.group(1), sql_file))

    return snapshots


# ── Registry builder ───────────────────────────────────────────────────────

def build_registry(snapshots: list[tuple[str, str, str, Path]], assets_dir: Path) -> dict:
    registry: dict = {}
    for tenant, env, date, _ in snapshots:
        asset_dir = assets_dir / tenant / (env or "") / date
        dialect_file = asset_dir / "dialect.txt"
        dialect = dialect_file.read_text(encoding="utf-8").strip() if dialect_file.exists() else "unknown"

        registry.setdefault(tenant, {})
        registry[tenant].setdefault(env, {"snapshots": [], "latest": "", "dialect": dialect})
        entry = registry[tenant][env]
        if date not in entry["snapshots"]:
            entry["snapshots"].append(date)
        entry["snapshots"].sort()
        entry["latest"] = entry["snapshots"][-1]
        if dialect != "unknown":
            entry["dialect"] = dialect

    return registry


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="Reprocess even if assets exist")
    args = ap.parse_args()

    if not TENANTS_DIR.exists():
        print(f"ERROR: tenants directory not found: {TENANTS_DIR}", file=sys.stderr)
        sys.exit(1)

    snapshots = discover_snapshots(TENANTS_DIR)
    if not snapshots:
        print("No snapshots found under tenants/", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(snapshots)} snapshot(s):\n")
    for tenant, env, date, sql_path in snapshots:
        label = f"{tenant}/{env}/{date}" if env else f"{tenant}/{date}"
        out_dir = ASSETS_DIR / tenant / (env or "") / date

        marker = out_dir / "tables.json"
        if marker.exists() and not args.force:
            print(f"  {label}  (skipped — already built)")
            continue

        print(f"  {label}  ({sql_path.stat().st_size // 1024} KB)")
        text    = sql_path.read_text(encoding="utf-8", errors="replace")
        dialect = detect_dialect(text)

        if dialect == "oracle":
            parsed = parse_oracle(text)
        elif dialect == "mssql":
            parsed = parse_mssql(text)
        else:
            print(f"    WARNING: unknown dialect, attempting Oracle parser")
            parsed = parse_oracle(text)

        write_assets(out_dir, parsed, dialect)

    registry = build_registry(snapshots, ASSETS_DIR)
    registry_path = ASSETS_DIR / "registry.json"
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRegistry written → {registry_path}")


if __name__ == "__main__":
    main()
