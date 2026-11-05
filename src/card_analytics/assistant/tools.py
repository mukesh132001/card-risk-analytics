"""Tools the assistant can call: search the docs, describe tables, run read-only SQL.

Guardrails on SQL, because a model writes it:
  * one SELECT (or WITH ... SELECT) statement, nothing that writes or changes settings
  * only the PII-masked reporting layer and the two non-personal dimensions
  * the database is opened read-only, and results are capped at MAX_ROWS rows
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pandas as pd

from card_analytics import catalog, mask
from card_analytics.assistant.knowledge import Retriever, default_retriever

ALLOWED_TABLES = {
    "rpt_dim_client", "rpt_dim_account", "rpt_fact_transactions", "rpt_fact_loans",
    "rpt_account_segments", "rpt_loan_scores", "dim_date", "dim_district",
}
EXTRA_TABLES = {
    "rpt_account_segments": {"account_key": "Masked account token", "segment": "Customer segment 1-4 from k-means (see docs/segmentation.md)"},
    "rpt_loan_scores": {
        "loan_id": "Loan key", "account_key": "Masked account token",
        "default_score": "Out-of-fold probability of default from the model",
        "flagged": "1 if default_score is at or above the alert threshold", "is_default": "1 if the loan defaulted",
    },
}
MAX_ROWS = 200
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum|reindex|grant|revoke|truncate|copy)\b", re.I
)
TABLE_REF = re.compile(r"\b(?:from|join)\s+([A-Za-z_][\w.]*)", re.I)
CTE_NAME = re.compile(r"(?:\bwith|,)\s*([A-Za-z_]\w*)\s+as\s*\(", re.I)


class SQLRejected(ValueError):
    pass


def db_url() -> str:
    return os.environ.get("CARDRISK_DB") or os.environ.get("DATABASE_URL") or "sqlite:///cardrisk.db"


def check_sql(sql: str) -> str:
    """Return the cleaned statement, or raise SQLRejected with a reason the model can act on."""
    clean = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip().rstrip(";").strip()
    if not clean:
        raise SQLRejected("empty query")
    if ";" in clean:
        raise SQLRejected("only one statement is allowed")
    if not re.match(r"^(select|with)\b", clean, re.I):
        raise SQLRejected("only SELECT queries are allowed")
    if FORBIDDEN.search(clean):
        raise SQLRejected(f"keyword not allowed: {FORBIDDEN.search(clean).group(1)}")
    ctes = {m.lower() for m in CTE_NAME.findall(clean)}
    tables = {t.lower() for t in TABLE_REF.findall(clean)} - ctes
    blocked = sorted(tables - ALLOWED_TABLES)
    if blocked:
        raise SQLRejected(f"table(s) not allowed: {', '.join(blocked)}. Use only: {', '.join(sorted(ALLOWED_TABLES))}")
    return clean


def connect_readonly(url: str):
    if url.startswith("sqlite:///"):
        path = Path(url.removeprefix("sqlite:///")).resolve()
        if not path.exists():
            raise FileNotFoundError(f"database not found: {path}")
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    from sqlalchemy import create_engine

    return create_engine(url, execution_options={"postgresql_readonly": True})


def run_sql(sql: str, url: str | None = None) -> dict:
    """Run one read-only SELECT on the reporting layer. Returns columns, rows (max 200) and a truncated flag."""
    try:
        clean = check_sql(sql)
    except SQLRejected as e:
        return {"error": f"query rejected: {e}"}
    try:
        df = pd.read_sql(f"select * from ({clean}) as q limit {MAX_ROWS + 1}", connect_readonly(url or db_url()))
    except Exception as e:  # surface the database error so the model can fix its query
        return {"error": f"query failed: {type(e).__name__}: {e}"}
    truncated = len(df) > MAX_ROWS
    df = df.head(MAX_ROWS)
    rows = [[None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in r] for r in df.itertuples(index=False)]
    return {"columns": list(df.columns), "rows": rows, "row_count": len(rows), "truncated": truncated}


def search_docs(query: str, k: int = 4, retriever: Retriever | None = None) -> list[dict]:
    """Find the most relevant doc sections. Cite results by their `source`."""
    hits = (retriever or default_retriever()).search(query, k)
    return [{"source": c.source, "title": c.title, "score": round(s, 3), "text": c.text} for c, s in hits]


def list_tables() -> dict:
    """Tables the SQL tool can read, with every column and its meaning."""
    out = {}
    for source in mask.REPORTING:
        target = mask.REPORTING[source][0]
        out[target] = {"grain": catalog.CATALOG[source]["grain"], "columns": {c: d for c, (d, _) in catalog.reporting_columns(source).items()}}
    for table in ("dim_date", "dim_district"):
        out[table] = {"grain": catalog.CATALOG[table]["grain"], "columns": {c: d for c, (d, _) in catalog.CATALOG[table]["columns"].items()}}
    for table, cols in EXTRA_TABLES.items():
        out[table] = {"grain": "one account" if table == "rpt_account_segments" else "one loan", "columns": cols}
    return out


# Tool definitions in the Anthropic Messages API format.
TOOL_SPECS = [
    {
        "name": "search_docs",
        "description": (
            "Search the platform documentation: data dictionary, lineage, reconciliation report, segmentation, "
            "model card, findings memo and README. Use for definitions, methodology, controls, assumptions and "
            "recommendations. Each result has a `source` to cite."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in plain words"},
                "k": {"type": "integer", "description": "Number of sections to return (default 4, max 8)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_tables",
        "description": "List the tables run_sql can query, with every column and what it means. Call this before writing SQL.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_sql",
        "description": (
            "Run ONE read-only SQLite SELECT against the PII-masked reporting layer to get numbers. Allowed tables: "
            + ", ".join(sorted(ALLOWED_TABLES))
            + f". Returns at most {MAX_ROWS} rows. Amounts are CZK. Join facts to dim_date on date_key."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single SELECT statement"}},
            "required": ["sql"],
        },
    },
]


def call_tool(name: str, args: dict) -> object:
    if name == "search_docs":
        return search_docs(args["query"], min(int(args.get("k", 4)), 8))
    if name == "list_tables":
        return list_tables()
    if name == "run_sql":
        return run_sql(args["sql"])
    return {"error": f"unknown tool {name}"}
