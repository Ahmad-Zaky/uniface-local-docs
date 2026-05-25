#!/bin/bash

_confirm_production() {
  echo "⚠️  You are about to run against PRODUCTION."
  read -r -p "Type 'yes' to continue: " confirm
  if [ "$confirm" != "yes" ]; then
    echo "Aborted." >&2
    return 1
  fi
}

# -------------------------------------------
# Oracle version of the schema dump function:
# -------------------------------------------

_oracle_validate_connection() {
  local host="$1" sid="$2" username="$3" password="$4"
  local missing=()
  [[ -z "$host" ]]     && missing+=("--host")
  [[ -z "$sid" ]]      && missing+=("--sid")
  [[ -z "$username" ]] && missing+=("--username")
  [[ -z "$password" ]] && missing+=("--password")
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Error: missing required connection options: ${missing[*]}" >&2
    echo "Run 'oracledumpschema --help' for usage." >&2
    return 1
  fi
}

_oracle_validate_files() {
  local sql_file="$1"
  if [[ ! -f "$sql_file" ]]; then
    echo "Error: SQL script not found at $sql_file" >&2
    echo "Run 'oracledumpschema --help' for usage." >&2
    return 1
  fi
}

_oracle_run() {
  local sql_file="$1" output_file="$2"
  local host="$3" port="$4" sid="$5" username="$6" password="$7"

  # Run from a temp directory so SPOOL writes there, then move the output
  local work_dir
  work_dir="$(mktemp -d)"

  cd "$work_dir" || { rm -rf "$work_dir"; return 1; }

  sqlplus64 -s "$username/$password@//$host:$port/$sid" @"$sql_file"
  local rc=$?

  cd - >/dev/null || true

  if [[ -f "$work_dir/schema.sql" ]]; then
    mv "$work_dir/schema.sql" "$output_file"
  else
    echo "Error: schema.sql was not produced" >&2
    rc=1
  fi

  rm -rf "$work_dir"
  return $rc
}

oracledumpschema() {
  local tenant="${TENANT:-xxx}"
  local env="${DB_ENV:-}"
  local host="${ORACLE_HOST:-}"
  local port="${ORACLE_PORT:-1521}"
  local sid="${ORACLE_SID:-}"
  local username="${ORACLE_USERNAME:-}"
  local password="${ORACLE_PASSWORD:-}"
  local sql_file="$PWD/oracle_dump_schema.sql"
  local output_file=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        cat <<'EOF'
Usage: oracledumpschema [OPTIONS]
       ./schema_dump.sh oracle [OPTIONS]

Dumps the schema of an Oracle database by running a SQL file through sqlplus64.
The SQL file is expected to use DBMS_METADATA.GET_DDL and SPOOL schema.sql.

Connection options:
  --host HOST           Oracle server hostname                    [env: ORACLE_HOST]
  --port PORT           Oracle server port (default: 1521)        [env: ORACLE_PORT]
  --sid SID             Oracle SID                                [env: ORACLE_SID]
  --username USER       Login username                            [env: ORACLE_USERNAME]
  --password PASS       Login password                            [env: ORACLE_PASSWORD]

Output options:
  --tenant NAME         Tenant name (default: xxx)                [env: TENANT]
  --env ENV             Environment name, e.g. stage or prod      [env: DB_ENV]
                        When set:   output goes to ./tenants/<tenant>/<env>/oracle-schema.sql
                        When unset: output goes to ./tenants/<tenant>/oracle-schema.sql
  --output-file PATH    Override output path entirely

File options:
  --sql-file PATH       Path to the SQL file to execute
                        Default: ./oracle_dump_schema.sql

Other:
  -h, --help            Show this help message and exit

Connection flags take precedence over environment variables.

How it works:
  Runs sqlplus64 from a temporary directory so that SPOOL schema.sql
  writes there, then moves the result to the output path specified.

Examples:
  oracledumpschema --host db.example.com --sid ORCL --username scott --password tiger
  # Output: ./tenants/xxx/oracle-schema.sql

  oracledumpschema --host db.example.com --sid ORCL --username scott --password tiger \
                   --tenant myco --env stage
  # Output: ./tenants/myco/stage/oracle-schema.sql

  ./schema_dump.sh oracle --host db.example.com --sid ORCL --username scott --password tiger

  ORACLE_HOST=db.example.com ORACLE_SID=ORCL ORACLE_USERNAME=scott ORACLE_PASSWORD=tiger \
    ./schema_dump.sh oracle
EOF
        return 0
        ;;
      --tenant)       tenant="$2";   shift 2 ;;
      --env)          env="$2";      shift 2 ;;
      --host)         host="$2";     shift 2 ;;
      --port)         port="$2";     shift 2 ;;
      --sid)          sid="$2";      shift 2 ;;
      --username)     username="$2"; shift 2 ;;
      --password)     password="$2"; shift 2 ;;
      --sql-file)     sql_file="$2"; shift 2 ;;
      --output-file)  output_file="$2"; shift 2 ;;
      *)
        echo "Error: unknown option '$1'" >&2
        echo "Run 'oracledumpschema --help' for usage." >&2
        return 1
        ;;
    esac
  done

  [[ "$env" == "prod" ]] && { _confirm_production || return 1; }

  if [[ -z "$output_file" ]]; then
    local output_dir
    if [[ -n "$env" ]]; then
      output_dir="$PWD/tenants/$tenant/$env"
    else
      output_dir="$PWD/tenants/$tenant"
    fi
    output_file="$output_dir/$(date +%Y-%m-%d)_schema.sql"
  fi

  _oracle_validate_connection "$host" "$sid" "$username" "$password" || return 1
  _oracle_validate_files "$sql_file"                                  || return 1

  # Resolve to absolute paths (so they work after cd into work_dir)
  sql_file="$(cd "$(dirname "$sql_file")" && pwd)/$(basename "$sql_file")"
  [[ "$output_file" != /* ]] && output_file="$PWD/$output_file"

  mkdir -p "$(dirname "$output_file")" || { echo "Error: cannot create output directory" >&2; return 1; }

  _oracle_run "$sql_file" "$output_file" "$host" "$port" "$sid" "$username" "$password"
  local rc=$?

  if [[ $rc -eq 0 ]] && [[ -f "$output_file" ]]; then
    echo "Schema written to: $output_file"
  else
    echo "Error: schema dump failed (exit code $rc)" >&2
  fi

  return $rc
}



# ------------------------------------------
# MSSQL version of the schema dump function:
# ------------------------------------------

_mssql_validate_connection() {
  local host="$1" database="$2" username="$3" password="$4"
  local missing=()
  [[ -z "$host" ]]     && missing+=("--host")
  [[ -z "$database" ]] && missing+=("--database")
  [[ -z "$username" ]] && missing+=("--username")
  [[ -z "$password" ]] && missing+=("--password")
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Error: missing required connection options: ${missing[*]}" >&2
    echo "Run 'mssqldumpschema --help' for usage." >&2
    return 1
  fi
}

_mssql_validate_files() {
  local sql_file="$1" script_path="$2" venv_path="$3"
  if [[ ! -f "$sql_file" ]]; then
    echo "Error: SQL script not found at $sql_file" >&2
    echo "Run 'mssqldumpschema --help' for usage." >&2
    return 1
  fi
  if [[ ! -f "$script_path" ]]; then
    echo "Error: Python runner not found at $script_path" >&2
    echo "Run 'mssqldumpschema --help' for usage." >&2
    return 1
  fi
  if [[ ! -f "$venv_path/bin/activate" ]]; then
    echo "Error: Python venv not found at $venv_path" >&2
    echo "Create it with: python3 -m venv \"$venv_path\" && source \"$venv_path/bin/activate\" && pip install pymssql && deactivate" >&2
    return 1
  fi
}

_mssql_run() {
  local sql_file="$1" output_file="$2" script_path="$3" venv_path="$4"
  local host="$5" port="$6" database="$7" username="$8" password="${9}"

  # shellcheck disable=SC1091
  source "$venv_path/bin/activate"

  export MSSQL_HOST="$host"
  export MSSQL_PORT="$port"
  export MSSQL_DATABASE="$database"
  export MSSQL_USERNAME="$username"
  export MSSQL_PASSWORD="$password"

  python3 "$script_path" "$sql_file" "$output_file"
  local rc=$?

  unset MSSQL_HOST MSSQL_PORT MSSQL_DATABASE MSSQL_USERNAME MSSQL_PASSWORD

  if command -v deactivate >/dev/null 2>&1; then
    deactivate
  fi

  return $rc
}

mssqldumpschema() {
  local tenant="${TENANT:-xxx}"
  local env="${DB_ENV:-}"
  local host="${MSSQL_HOST:-}"
  local port="${MSSQL_PORT:-1433}"
  local database="${MSSQL_DATABASE:-}"
  local username="${MSSQL_USERNAME:-}"
  local password="${MSSQL_PASSWORD:-}"
  local sql_file="$PWD/mssql_dump_schema.sql"
  local output_file=""
  local script_path="$PWD/mssql_runner.py"
  local venv_path="$PWD/.venv"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        cat <<'EOF'
Usage: mssqldumpschema [OPTIONS]
       ./schema_dump.sh mssql [OPTIONS]

Dumps the schema of a Microsoft SQL Server database by running a SQL file
through a Python runner script.
Uses a Python virtual environment for dependencies (pymssql).

Connection options:
  --host HOST           MSSQL server hostname                    [env: MSSQL_HOST]
  --port PORT           MSSQL server port (default: 1433)        [env: MSSQL_PORT]
  --database DB         Database name                            [env: MSSQL_DATABASE]
  --username USER       Login username                           [env: MSSQL_USERNAME]
  --password PASS       Login password                           [env: MSSQL_PASSWORD]

Output options:
  --tenant NAME         Tenant name (default: xxx)               [env: TENANT]
  --env ENV             Environment name, e.g. stage or prod     [env: DB_ENV]
                        When set:   output goes to ./tenants/<tenant>/<env>/mssql-schema.sql
                        When unset: output goes to ./tenants/<tenant>/mssql-schema.sql
  --output-file PATH    Override output path entirely

File options:
  --sql-file PATH       Path to the SQL file to execute
                        Default: ./mssql_dump_schema.sql
  --script-path PATH    Path to the Python runner script
                        Default: ./mssql_runner.py
  --venv-path PATH      Path to the Python virtual environment directory
                        Default: ./.venv

Other:
  -h, --help            Show this help message and exit

Connection flags take precedence over environment variables.

Examples:
  mssqldumpschema --host db.example.com --database MyDB --username sa --password secret
  # Output: ./tenants/xxx/mssql-schema.sql

  mssqldumpschema --host db.example.com --database MyDB --username sa --password secret \
                  --tenant myco --env stage
  # Output: ./tenants/myco/stage/mssql-schema.sql

  ./schema_dump.sh mssql --host db.example.com --database MyDB --username sa --password secret

  MSSQL_HOST=db.example.com MSSQL_DATABASE=MyDB MSSQL_USERNAME=sa MSSQL_PASSWORD=secret \
    ./schema_dump.sh mssql

One-time venv setup:
  python3 -m venv .venv
  source .venv/bin/activate
  pip install pymssql
  deactivate
EOF
        return 0
        ;;
      --tenant)       tenant="$2";      shift 2 ;;
      --env)          env="$2";         shift 2 ;;
      --host)         host="$2";        shift 2 ;;
      --port)         port="$2";        shift 2 ;;
      --database)     database="$2";    shift 2 ;;
      --username)     username="$2";    shift 2 ;;
      --password)     password="$2";    shift 2 ;;
      --sql-file)     sql_file="$2";    shift 2 ;;
      --output-file)  output_file="$2"; shift 2 ;;
      --script-path)  script_path="$2"; shift 2 ;;
      --venv-path)    venv_path="$2";   shift 2 ;;
      *)
        echo "Error: unknown option '$1'" >&2
        echo "Run 'mssqldumpschema --help' for usage." >&2
        return 1
        ;;
    esac
  done

  [[ "$env" == "prod" ]] && { _confirm_production || return 1; }

  if [[ -z "$output_file" ]]; then
    local output_dir
    if [[ -n "$env" ]]; then
      output_dir="$PWD/tenants/$tenant/$env"
    else
      output_dir="$PWD/tenants/$tenant"
    fi
    output_file="$output_dir/$(date +%Y-%m-%d)_schema.sql"
  fi

  _mssql_validate_connection "$host" "$database" "$username" "$password" || return 1
  _mssql_validate_files "$sql_file" "$script_path" "$venv_path"          || return 1

  # Resolve to absolute paths (so they work after any cd)
  sql_file="$(cd "$(dirname "$sql_file")" && pwd)/$(basename "$sql_file")"
  script_path="$(cd "$(dirname "$script_path")" && pwd)/$(basename "$script_path")"
  venv_path="$(cd "$venv_path" && pwd)"
  [[ "$output_file" != /* ]] && output_file="$PWD/$output_file"

  mkdir -p "$(dirname "$output_file")" || { echo "Error: cannot create output directory" >&2; return 1; }

  _mssql_run "$sql_file" "$output_file" "$script_path" "$venv_path" \
             "$host" "$port" "$database" "$username" "$password"
  local rc=$?

  if [[ $rc -eq 0 ]] && [[ -f "$output_file" ]]; then
    echo "Schema written to: $output_file"
  else
    echo "Error: schema dump failed (exit code $rc)" >&2
  fi

  return $rc
}

# Run directly when executed as a script (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    oracle) shift; oracledumpschema "$@" ;;
    mssql)  shift; mssqldumpschema  "$@" ;;
    -h|--help)
      printf 'Usage: %s <oracle|mssql> [OPTIONS]\n' "$(basename "$0")"
      printf '  Run with oracle --help or mssql --help for details.\n'
      exit 0
      ;;
    *)
      printf 'Error: expected oracle or mssql as first argument\n' >&2
      printf 'Run %s --help for usage.\n' "$(basename "$0")" >&2
      exit 1
      ;;
  esac
  exit $?
fi