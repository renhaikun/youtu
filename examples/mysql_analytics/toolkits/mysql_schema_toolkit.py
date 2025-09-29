import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any

from utu.tools.base import AsyncBaseToolkit, register_tool


@dataclass
class MysqlConn:
    host: str = ""
    port: int = 3306
    user: str = ""
    password: str = ""
    database: str | None = None


def _run_mysql(conn: MysqlConn, sql: str, database: str | None = None, include_header: bool = False) -> str:
    """Run a mysql CLI command and return raw stdout (UTF-8).

    - Uses MYSQL_PWD env to avoid password echo.
    - Uses -N -B to get tab-separated rows without headers.
    """
    db = database or conn.database
    if not (conn.host and conn.user and conn.password):
        raise ValueError("MySQL connection is not fully configured (host/user/password required)")
    args = [
        "mysql",
        "-h",
        conn.host,
        "-P",
        str(conn.port),
        "-u",
        conn.user,
        "-B",
        "-e",
        sql,
    ]
    if not include_header:
        args.insert(7, "-N")  # add -N before -B when we don't want header
    if db:
        args.extend(["-D", db])
    env = os.environ.copy()
    # mysql respects MYSQL_PWD when no -p is provided
    env["MYSQL_PWD"] = conn.password
    try:
        out = subprocess.check_output(args, env=env, stderr=subprocess.STDOUT)
        return out.decode("utf-8", errors="ignore")
    except subprocess.CalledProcessError as e:
        msg = e.output.decode("utf-8", errors="ignore")
        raise RuntimeError(f"mysql CLI failed: {msg}") from e


class MysqlSchemaToolkit(AsyncBaseToolkit):
    """MySQL schema exploration helpers using the local mysql CLI.

    No external Python DB driver required. Ensure `mysql` command exists on PATH.
    """

    def __init__(self, config=None):
        super().__init__(config)
        self.conn: MysqlConn = MysqlConn()
        self.schema_cache: dict[str, Any] = {}
        self.active_tables: list[str] = []

    # ------------------------------
    # helpers
    # ------------------------------
    def _sanitize_identifier(self, name: str, prefix: str = "T") -> str:
        import re
        s = (name or "").strip()
        s = re.sub(r"[^A-Za-z0-9_]", "_", s)
        if not s or not re.match(r"[A-Za-z]", s[0]):
            s = f"{prefix}_{s}" if s else f"{prefix}_"
        return s

    def _normalize_type(self, t: str) -> str:
        tl = (t or "").lower()
        if "int" in tl:
            return "int"
        if any(x in tl for x in ["decimal", "numeric", "float", "double", "real"]):
            return "float"
        if any(x in tl for x in ["bool", "tinyint(1)"]):
            return "bool"
        if any(x in tl for x in ["date", "time", "year", "timestamp"]):
            return "datetime"
        if any(x in tl for x in ["char", "text", "blob", "binary", "json"]):
            return "string"
        return "string"

    @register_tool
    async def set_connection(self, host: str, port: int, user: str, password: str, database: str | None = None) -> str:
        """Set connection info. Returns a short success message.

        Args:
            host: DB host
            port: DB port (int)
            user: DB user (read-only recommended)
            password: DB password
            database: Optional database/schema name
        """
        self.conn = MysqlConn(host=host, port=port, user=user, password=password, database=database)
        # quick connectivity smoke test
        _ = _run_mysql(self.conn, "SELECT 1;")
        return "mysql connection ok"

    @register_tool
    async def list_databases(self, exclude_system: bool = True) -> list[str]:
        """List databases. Optionally exclude system schemas."""
        raw = _run_mysql(self.conn, "SHOW DATABASES;")
        dbs = [line.strip() for line in raw.splitlines() if line.strip()]
        if exclude_system:
            system = {"information_schema", "performance_schema", "sys", "mysql"}
            dbs = [d for d in dbs if d not in system]
        return dbs

    @register_tool
    async def pick_candidate_databases(self, top_k: int = 10) -> list[dict]:
        """Rank databases by table count to help user pick one."""
        sql = (
            "SELECT table_schema, COUNT(*) AS n "
            "FROM information_schema.tables GROUP BY table_schema ORDER BY n DESC LIMIT %d;" % top_k
        )
        raw = _run_mysql(self.conn, sql)
        items = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                items.append({"database": parts[0], "tables": int(parts[1])})
        # filter out system schemas if present
        system = {"information_schema", "performance_schema", "sys", "mysql"}
        return [x for x in items if x["database"] not in system]

    @register_tool
    async def introspect_schema(self, database: str | None = None) -> dict:
        """Collect tables, columns and foreign keys from INFORMATION_SCHEMA."""
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set; call set_connection or pass database")
        # tables
        sql_tables = (
            "SELECT table_name FROM information_schema.tables WHERE table_schema = '" + db + "' ORDER BY table_name;"
        )
        t_raw = _run_mysql(self.conn, sql_tables)
        tables = [t.strip() for t in t_raw.splitlines() if t.strip()]
        # columns
        sql_cols = (
            "SELECT table_name, column_name, column_key, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_schema = '"
            + db
            + "' ORDER BY table_name, ordinal_position;"
        )
        c_raw = _run_mysql(self.conn, sql_cols)
        columns = []
        for line in c_raw.splitlines():
            parts = line.split("\t")
            if len(parts) == 5:
                columns.append(
                    {
                        "table": parts[0],
                        "name": parts[1],
                        "key": parts[2],
                        "type": parts[3],
                        "nullable": parts[4],
                    }
                )
        # foreign keys
        sql_fks = (
            "SELECT table_name, column_name, referenced_table_name, referenced_column_name "
            "FROM information_schema.key_column_usage WHERE table_schema = '"
            + db
            + "' AND referenced_table_name IS NOT NULL;"
        )
        f_raw = _run_mysql(self.conn, sql_fks)
        fks = []
        for line in f_raw.splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                fks.append(
                    {
                        "table": parts[0],
                        "column": parts[1],
                        "ref_table": parts[2],
                        "ref_column": parts[3],
                    }
                )
        meta = {"database": db, "tables": tables, "columns": columns, "fks": fks}
        self.schema_cache[db] = meta
        return meta

    @register_tool
    async def generate_er_mermaid(self, database: str | None = None, tables: list[str] | None = None) -> str:
        """Generate Mermaid erDiagram text from cached or freshly introspected schema.

        If `tables` is provided, only include those tables; otherwise if `self.active_tables`
        is set, filter to them; else include all tables.
        """
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        meta = self.schema_cache.get(db) or await self.introspect_schema(db)
        include_tables = tables or (self.active_tables if self.active_tables else meta["tables"])
        include_tables = [t for t in include_tables if t in meta["tables"]]

        # build sanitize maps for entity and columns
        tname_map = {t: self._sanitize_identifier(t, prefix="T") for t in meta["tables"]}
        # Build entities
        lines = ["erDiagram"]
        # relationships: parent ||--o{ child
        for fk in meta["fks"]:
            parent = fk["ref_table"]
            child = fk["table"]
            if parent not in include_tables or child not in include_tables:
                continue
            rel = f"  {tname_map[parent]} ||--o{{ {tname_map[child]} : {self._sanitize_identifier(fk['column'], 'C')}__TO__{self._sanitize_identifier(fk['ref_column'], 'C')}"
            lines.append(rel)
        # entity definitions
        cols_by_table: dict[str, list[dict]] = {}
        for col in meta["columns"]:
            cols_by_table.setdefault(col["table"], []).append(col)
        for t in include_tables:
            lines.append(f"  {tname_map[t]} {{")
            for c in cols_by_table.get(t, []):
                dtype = self._normalize_type(c.get("type", ""))
                cname = self._sanitize_identifier(c.get("name", ""), prefix="C")
                suffix = " PK" if c.get("key") == "PRI" else ""
                lines.append(f"    {dtype} {cname}{suffix}")
            lines.append("  }")
        mermaid_body = "\n".join(lines)
        # Always return fenced code block to ensure rendering
        return f"```mermaid\n{mermaid_body}\n```"

    @register_tool
    async def list_tables(self, database: str | None = None) -> list[str]:
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        meta = self.schema_cache.get(db) or await self.introspect_schema(db)
        return meta["tables"]

    @register_tool
    async def list_tables_like(self, pattern: str, database: str | None = None) -> list[str]:
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        # naive pattern matching in python
        import re
        regex = re.compile(pattern.replace("%", ".*"))
        tables = await self.list_tables(db)
        return [t for t in tables if regex.search(t)]

    @register_tool
    async def list_tables_detailed(self, database: str | None = None) -> list[dict]:
        """List tables with approximate row counts from information_schema (fast, may be approximate)."""
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        sql = (
            "SELECT table_name, table_rows FROM information_schema.tables "
            f"WHERE table_schema = '{db}' ORDER BY table_rows DESC, table_name;"
        )
        raw = _run_mysql(self.conn, sql)
        items = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                try:
                    items.append({"table": parts[0], "rows": int(parts[1])})
                except Exception:
                    items.append({"table": parts[0], "rows": parts[1]})
        return items

    @register_tool
    async def show_create_table(self, table: str, database: str | None = None) -> str:
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        sql = f"SHOW CREATE TABLE `{db}`.`{table}`;"
        return _run_mysql(self.conn, sql, database=db, include_header=True)

    @register_tool
    async def show_indexes(self, table: str, database: str | None = None) -> str:
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        sql = f"SHOW INDEX FROM `{db}`.`{table}`;"
        return _run_mysql(self.conn, sql, database=db, include_header=True)

    @register_tool
    async def get_table_row_count(self, table: str, database: str | None = None) -> int:
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        sql = f"SELECT COUNT(*) AS n FROM `{db}`.`{table}`"  # count could be heavy on huge tables
        try:
            raw = _run_mysql(self.conn, sql, database=db, include_header=True)
            lines = [l for l in raw.splitlines() if l.strip()]
            if len(lines) >= 2:
                return int(lines[1].split("\t")[0])
        except Exception:
            # fallback to information_schema approximate rows
            raw = _run_mysql(self.conn, (
                "SELECT table_rows FROM information_schema.tables "
                f"WHERE table_schema = '{db}' AND table_name = '{table}'"
            ))
            lines = [l for l in raw.splitlines() if l.strip()]
            if lines:
                try:
                    return int(lines[0])
                except Exception:
                    return -1
        return -1

    @register_tool
    async def find_semantic_tables(self, keywords: list[str], database: str | None = None, top_k: int = 5) -> list[dict]:
        """Find candidate tables by matching keywords to table names and column names.

        Simple scoring: +3 per keyword in table name; +1 per keyword in any column name.
        """
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        meta = self.schema_cache.get(db) or await self.introspect_schema(db)
        cols_by_table: dict[str, list[str]] = {}
        for c in meta["columns"]:
            cols_by_table.setdefault(c["table"], []).append(c["name"])
        def score_table(t: str) -> int:
            s = 0
            name_l = t.lower()
            for kw in keywords:
                k = kw.lower()
                if k in name_l:
                    s += 3
                for col in cols_by_table.get(t, []):
                    if k in col.lower():
                        s += 1
            return s
        scored = [{"table": t, "score": score_table(t)} for t in meta["tables"]]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return [s for s in scored if s["score"] > 0][:top_k]

    @register_tool
    async def set_active_tables(self, tables: list[str]) -> dict:
        """Set active tables to focus analysis and ER generation."""
        self.active_tables = tables
        return {"ok": True, "active_tables": self.active_tables}

    @register_tool
    async def get_active_selection(self) -> dict:
        return {"database": self.conn.database, "tables": self.active_tables}

    @register_tool
    async def export_query_tsv(self, sql: str, out_path: str, database: str | None = None, limit: int | None = None) -> str:
        """Export a query result to a local TSV file for downstream Python.

        Args:
            sql: SELECT statement (no semicolons needed)
            out_path: local file path to write
            database: optional override database
            limit: optional LIMIT
        Returns: the file path written
        """
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        sql_stmt = sql.strip()
        if sql_stmt.endswith(";"):
            sql_stmt = sql_stmt[:-1]
        if limit is not None and limit > 0:
            sql_stmt = f"SELECT * FROM ( {sql_stmt} ) AS _sub LIMIT {int(limit)}"
        raw = _run_mysql(self.conn, sql_stmt, database=db, include_header=True)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(raw)
        return out_path

    @register_tool
    async def exec_sql(self, sql: str, database: str | None = None, limit: int = 2000) -> dict:
        """Execute a SELECT query and return a small, safe preview as JSON rows.

        Safety:
        - Only allow SELECT statements; deny non-SELECT.
        - Apply LIMIT (default 2000) if not present to avoid heavy queries.
        - No locks: Do not allow FOR UPDATE.
        """
        db = database or self.conn.database
        if not db:
            raise ValueError("database is not set")
        stmt = (sql or "").strip().rstrip(";")
        up = stmt.upper()
        if not up.startswith("SELECT"):
            raise ValueError("Only SELECT is allowed in exec_sql")
        if " FOR UPDATE" in up:
            raise ValueError("FOR UPDATE is not allowed")
        if " LIMIT " not in up:
            stmt = f"SELECT * FROM ( {stmt} ) AS _sub LIMIT {int(limit)}"
        raw = _run_mysql(self.conn, stmt, database=db, include_header=True)
        lines = [l for l in raw.splitlines() if l.strip()]
        if not lines:
            return {"columns": [], "rows": [], "row_count": 0}
        header = lines[0].split("\t")
        rows = [ln.split("\t") for ln in lines[1:]]
        return {"columns": header, "rows": rows, "row_count": len(rows)}

    @register_tool
    async def save_session(self, path: str = "./.utu_mysql_session.json") -> dict:
        """Persist current session info (without password) to a local json file.

        NOTE: For security, password is NOT saved. On reload, you must re-enter password.
        Saved fields: host, port, user, database, active_tables.
        """
        data = {
            "host": self.conn.host,
            "port": self.conn.port,
            "user": self.conn.user,
            "database": self.conn.database,
            "active_tables": self.active_tables,
        }
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": path}

    @register_tool
    async def load_session(self, path: str = "./.utu_mysql_session.json") -> dict:
        """Load session info from local json (without password).

        You still need to call `set_connection` with password once per process.
        """
        import json, os
        if not os.path.exists(path):
            return {"ok": False, "message": f"session file not found: {path}"}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.conn.host = data.get("host", self.conn.host)
        self.conn.port = data.get("port", self.conn.port)
        self.conn.user = data.get("user", self.conn.user)
        self.conn.database = data.get("database", self.conn.database)
        self.active_tables = data.get("active_tables", [])
        return {"ok": True, "data": data}
