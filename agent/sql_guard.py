"""
SQL safety rails (blueprint §6.4) — deterministic AST validation via sqlglot.

Belt-and-braces in front of the read-only DB role: reject anything that is not
a single read-only SELECT over whitelisted dim_*/fact_*/bridge_* tables, and
cap row count. Raises UnsafeSQL on violation so the pipeline can refuse.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_PREFIXES = ("dim_", "fact_", "bridge_", "v_")  # v_ = semantic-layer views
# Whitelisted set-returning semantic-layer functions (e.g. arbitrary-range metrics
# computed from fact_nav with the one verified 口径). Callable in FROM/LATERAL or a
# scalar subquery; anything else in a table position is rejected.
ALLOWED_FUNCTIONS = ("f_fund_perf_range",)
DEFAULT_LIMIT = 10_000


class UnsafeSQL(Exception):
    """Raised when generated SQL fails a safety rail."""


def _check_function(node: "exp.Anonymous") -> None:
    """Allow only whitelisted set-returning functions in a table position."""
    fn = (node.name or "").lower()
    if fn not in ALLOWED_FUNCTIONS:
        raise UnsafeSQL(f"function not whitelisted: {fn}")


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
        # a table-valued function call (FROM/subquery f_xxx(...)) parses as a Table
        # whose .this is the function — whitelist by function name, not prefix.
        if isinstance(table.this, exp.Anonymous):
            _check_function(table.this)
            continue
        name = (table.name or "").lower()
        schema = (table.db or "").lower()
        if schema and schema not in ("public", ""):
            raise UnsafeSQL(f"schema not allowed: {schema}")
        if not name.startswith(ALLOWED_PREFIXES):
            raise UnsafeSQL(f"table not whitelisted: {table.name}")

    # LATERAL f_xxx(...) parses under exp.Lateral, not exp.Table — check it too so a
    # non-whitelisted set-returning function can't sneak in via LATERAL.
    for lat in stmt.find_all(exp.Lateral):
        if isinstance(lat.this, exp.Anonymous):
            _check_function(lat.this)

    # Cap rows if the model didn't.
    if not stmt.args.get("limit"):
        stmt = stmt.limit(limit)

    return stmt.sql(dialect="postgres")
