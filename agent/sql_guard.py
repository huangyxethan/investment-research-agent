"""
SQL safety rails (blueprint §6.4) — deterministic AST validation via sqlglot.

Belt-and-braces in front of the read-only DB role: reject anything that is not
a single read-only SELECT over whitelisted dim_*/fact_*/bridge_* tables, and
cap row count. Raises UnsafeSQL on violation so the pipeline can refuse.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_PREFIXES = ("dim_", "fact_", "bridge_")
DEFAULT_LIMIT = 10_000


class UnsafeSQL(Exception):
    """Raised when generated SQL fails a safety rail."""


def guard(sql: str, *, limit: int = DEFAULT_LIMIT) -> str:
    """Validate and normalise. Returns the safe SQL (LIMIT injected if absent)."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception as e:  # parse failure is itself unsafe
        raise UnsafeSQL(f"could not parse SQL: {e}") from e

    if len(statements) != 1:
        raise UnsafeSQL("exactly one statement is allowed")

    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        raise UnsafeSQL(f"only SELECT is allowed, got {type(stmt).__name__}")

    # No SELECT ... INTO (a write).
    if stmt.args.get("into"):
        raise UnsafeSQL("SELECT INTO is not allowed")

    # Every referenced table must be whitelisted; no catalog/system schemas.
    for table in stmt.find_all(exp.Table):
        name = (table.name or "").lower()
        schema = (table.db or "").lower()
        if schema and schema not in ("public", ""):
            raise UnsafeSQL(f"schema not allowed: {schema}")
        if not name.startswith(ALLOWED_PREFIXES):
            raise UnsafeSQL(f"table not whitelisted: {table.name}")

    # Cap rows if the model didn't.
    if not stmt.args.get("limit"):
        stmt = stmt.limit(limit)

    return stmt.sql(dialect="postgres")
