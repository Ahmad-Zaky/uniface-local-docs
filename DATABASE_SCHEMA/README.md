# schema_dump.sh

Dumps the DDL schema of an **Oracle** or **Microsoft SQL Server** database to a `.sql` file.

---

## Prerequisites

| Database | Requirement |
|----------|-------------|
| Oracle   | `sqlplus64` installed and on `$PATH` |
| MSSQL    | Python 3, a virtual environment with `pymssql` installed |

### One-time MSSQL venv setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pymssql
deactivate
```

---

## Making the script executable

```bash
chmod +x schema_dump.sh
```

---

## Usage

```
./schema_dump.sh <oracle|mssql> [OPTIONS]
./schema_dump.sh --help
```

Pass `oracle` or `mssql` as the first argument to select the database type,
followed by the options for that database.

---

## Oracle

### Options

| Flag | Description | Default | Env var |
|------|-------------|---------|---------|
| `--host HOST` | Oracle server hostname | *(required)* | `ORACLE_HOST` |
| `--port PORT` | Oracle server port | `1521` | `ORACLE_PORT` |
| `--sid SID` | Oracle SID | *(required)* | `ORACLE_SID` |
| `--username USER` | Login username | *(required)* | `ORACLE_USERNAME` |
| `--password PASS` | Login password | *(required)* | `ORACLE_PASSWORD` |
| `--tenant NAME` | Tenant name | `xxx` | `TENANT` |
| `--env ENV` | Environment (e.g. `stage`, `prod`); `prod` triggers a confirmation prompt | *(empty)* | `DB_ENV` |
| `--sql-file PATH` | SQL file to execute | `./oracle_dump_schema.sql` | — |
| `--output-file PATH` | Override output path entirely | *(computed)* | — |

### Output path

| `--env` set? | Output path |
|--------------|-------------|
| No | `./tenants/<tenant>/YYYY-MM-DD_schema.sql` |
| Yes | `./tenants/<tenant>/<env>/YYYY-MM-DD_schema.sql` |

The directory is created automatically.

### How it works

The script runs `sqlplus64` from a temporary directory so that the `SPOOL schema.sql`
directive inside the SQL file writes there, then moves the result to the specified
output path.

### Examples

**Minimal — uses default tenant `xxx`, no environment:**
```bash
./schema_dump.sh oracle \
  --host db.example.com \
  --sid ORCL \
  --username scott \
  --password tiger
# Output: ./tenants/xxx/YYYY-MM-DD_schema.sql
```

**With tenant and environment:**
```bash
./schema_dump.sh oracle \
  --host db.example.com \
  --sid ORCL \
  --username scott \
  --password tiger \
  --tenant myco \
  --env stage
# Output: ./tenants/myco/stage/YYYY-MM-DD_schema.sql
```

**Non-default port:**
```bash
./schema_dump.sh oracle \
  --host db.example.com \
  --port 1522 \
  --sid ORCL \
  --username scott \
  --password tiger \
  --tenant myco
# Output: ./tenants/myco/YYYY-MM-DD_schema.sql
```

**Custom SQL file:**
```bash
./schema_dump.sh oracle \
  --host db.example.com \
  --sid ORCL \
  --username scott \
  --password tiger \
  --sql-file ~/scripts/my_oracle_dump.sql
```

**Override output path entirely:**
```bash
./schema_dump.sh oracle \
  --host db.example.com \
  --sid ORCL \
  --username scott \
  --password tiger \
  --output-file ~/dumps/YYYY-MM-DD_schema.sql
```

**Using environment variables instead of flags:**
```bash
export ORACLE_HOST=db.example.com
export ORACLE_SID=ORCL
export ORACLE_USERNAME=scott
export ORACLE_PASSWORD=tiger
export TENANT=myco
export DB_ENV=prod

./schema_dump.sh oracle
# Output: ./tenants/myco/prod/YYYY-MM-DD_schema.sql
```

**Inline environment variables:**
```bash
ORACLE_HOST=db.example.com ORACLE_SID=ORCL \
ORACLE_USERNAME=scott ORACLE_PASSWORD=tiger \
TENANT=myco DB_ENV=stage \
  ./schema_dump.sh oracle
```

**In-shell (sourced) usage:**
```bash
source schema_dump.sh
oracledumpschema --host db.example.com --sid ORCL --username scott --password tiger --tenant myco
```

---

## MSSQL

### Options

| Flag | Description | Default | Env var |
|------|-------------|---------|---------|
| `--host HOST` | MSSQL server hostname | *(required)* | `MSSQL_HOST` |
| `--port PORT` | MSSQL server port | `1433` | `MSSQL_PORT` |
| `--database DB` | Database name | *(required)* | `MSSQL_DATABASE` |
| `--username USER` | Login username | *(required)* | `MSSQL_USERNAME` |
| `--password PASS` | Login password | *(required)* | `MSSQL_PASSWORD` |
| `--tenant NAME` | Tenant name | `xxx` | `TENANT` |
| `--env ENV` | Environment (e.g. `stage`, `prod`); `prod` triggers a confirmation prompt | *(empty)* | `DB_ENV` |
| `--sql-file PATH` | SQL file to execute | `./mssql_dump_schema.sql` | — |
| `--output-file PATH` | Override output path entirely | *(computed)* | — |
| `--script-path PATH` | Python runner script | `./mssql_runner.py` | — |
| `--venv-path PATH` | Python virtual environment directory | `./.venv` | — |

### Output path

| `--env` set? | Output path |
|--------------|-------------|
| No | `./tenants/<tenant>/YYYY-MM-DD_schema.sql` |
| Yes | `./tenants/<tenant>/<env>/YYYY-MM-DD_schema.sql` |

The directory is created automatically.

### How it works

The script activates the Python virtual environment, exports the connection details
as environment variables, and runs `mssql_runner.py`, which connects via `pymssql`
and executes the SQL file. Environment variables are unset and the venv is
deactivated after the run.

### Examples

**Minimal — uses default tenant `xxx`, no environment:**
```bash
./schema_dump.sh mssql \
  --host db.example.com \
  --database MyDB \
  --username sa \
  --password secret
# Output: ./tenants/xxx/YYYY-MM-DD_schema.sql
```

**With tenant and environment:**
```bash
./schema_dump.sh mssql \
  --host db.example.com \
  --database MyDB \
  --username sa \
  --password secret \
  --tenant myco \
  --env stage
# Output: ./tenants/myco/stage/YYYY-MM-DD_schema.sql
```

**Non-default port:**
```bash
./schema_dump.sh mssql \
  --host db.example.com \
  --port 1434 \
  --database MyDB \
  --username sa \
  --password secret \
  --tenant myco
# Output: ./tenants/myco/YYYY-MM-DD_schema.sql
```

**Custom SQL file and Python runner:**
```bash
./schema_dump.sh mssql \
  --host db.example.com \
  --database MyDB \
  --username sa \
  --password secret \
  --sql-file ~/scripts/my_mssql_dump.sql \
  --script-path ~/tools/mssql_runner.py
```

**Custom venv location:**
```bash
./schema_dump.sh mssql \
  --host db.example.com \
  --database MyDB \
  --username sa \
  --password secret \
  --venv-path ~/envs/mssql-env
```

**Override output path entirely:**
```bash
./schema_dump.sh mssql \
  --host db.example.com \
  --database MyDB \
  --username sa \
  --password secret \
  --output-file ~/dumps/YYYY-MM-DD_schema.sql
```

**Using environment variables instead of flags:**
```bash
export MSSQL_HOST=db.example.com
export MSSQL_DATABASE=MyDB
export MSSQL_USERNAME=sa
export MSSQL_PASSWORD=secret
export TENANT=myco
export DB_ENV=prod

./schema_dump.sh mssql
# Output: ./tenants/myco/prod/YYYY-MM-DD_schema.sql
```

**Inline environment variables:**
```bash
MSSQL_HOST=db.example.com MSSQL_DATABASE=MyDB \
MSSQL_USERNAME=sa MSSQL_PASSWORD=secret \
TENANT=myco DB_ENV=stage \
  ./schema_dump.sh mssql
```

**In-shell (sourced) usage:**
```bash
source schema_dump.sh
mssqldumpschema --host db.example.com --database MyDB --username sa --password secret --tenant myco
```

---

## Built-in help

```bash
# Top-level help
./schema_dump.sh --help

# Oracle help
./schema_dump.sh oracle --help

# MSSQL help
./schema_dump.sh mssql --help
```

---

## Flag vs environment variable precedence

Flags always take precedence over environment variables. This lets you set a
baseline via exported variables and override individual values per run:

```bash
export MSSQL_HOST=db.example.com
export MSSQL_DATABASE=MyDB
export MSSQL_USERNAME=sa
export MSSQL_PASSWORD=secret
export TENANT=myco
export DB_ENV=stage

# Override only the environment for this run
./schema_dump.sh mssql --env prod
# Output: ./tenants/myco/prod/YYYY-MM-DD_schema.sql
```

The `TENANT` and `DB_ENV` environment variables are shared between the Oracle
and MSSQL subcommands.

---

## Production safety

When `--env prod` (or `DB_ENV=prod`) is set, the script requires explicit
confirmation before running, regardless of database type:

```
⚠️  You are about to run against PRODUCTION.
Type 'yes' to continue:
```

Any answer other than `yes` aborts immediately with a non-zero exit code.
This applies to both Oracle and MSSQL.
