"""Load one typed synthetic bundle independently into the two engines."""

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import sql

from migration_verifier.engines import ROOT, EngineError, source_connection

SCHEMAS = {
    "merchants": [("merchant_id", "text"), ("settlement_tz", "text")],
    "authorisations": [
        ("auth_id", "text"),
        ("merchant_id", "text"),
        ("occurred_at_utc", "instant"),
        ("amount", "decimal"),
        ("currency", "text"),
        ("status", "text"),
    ],
    "captures": [
        ("capture_id", "text"),
        ("auth_id", "text"),
        ("captured_at_utc", "instant"),
        ("amount", "decimal"),
    ],
    "refunds": [
        ("refund_id", "text"),
        ("capture_id", "text"),
        ("refunded_at_utc", "instant"),
        ("amount", "decimal"),
    ],
    "settlement_events": [
        ("event_id", "text"),
        ("merchant_id", "text"),
        ("currency", "text"),
        ("settlement_date", "date"),
        ("source_ref", "text"),
        ("net_amount", "decimal"),
        ("recorded_at_utc", "instant"),
    ],
    "migration_params": [("as_of_utc", "instant")],
}
FIXTURES = ("repair", "late", "ties")
PG_TYPES = {"text": "TEXT", "instant": "TIMESTAMPTZ", "decimal": "NUMERIC(18,2)", "date": "DATE"}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def fixture_rows(name: str) -> dict[str, list[list[Any]]]:
    if name not in (*FIXTURES, "row_limit"):
        raise ValueError(f"Unknown fixture ID {name!r}.")
    base: dict[str, list[list[Any]]] = json.loads((ROOT / "fixtures/base.json").read_bytes())
    rows = deepcopy(base)
    if name == "late":
        rows["captures"].append(["C8", "A2", "2026-01-08T00:00:00.000001+00:00", "2.00"])
        rows["refunds"].append(["R4", "C3", "2026-01-08T08:00:00+00:00", "1.01"])
        rows["migration_params"] = [["2026-01-09T12:00:00+00:00"]]
    elif name == "ties":
        rows["captures"].append(["C0", "A1", "2026-01-07T22:00:00+00:00", "1.00"])
        rows["settlement_events"].append(
            ["E0", "M1", "ZAR", "2026-01-08", "A", "100.10", "2026-01-08T10:00:00+00:00"]
        )
        rows["authorisations"].append(
            ["A8", "M1", "2026-01-08T08:00:00+00:00", "8.00", "ZAR", "APPROVED"]
        )
    elif name == "row_limit":
        rows["settlement_events"] = [
            [f"E{index:03}", "M1", "ZAR", "2026-01-08", "A", "1.00", "2026-01-08T10:00:00+00:00"]
            for index in range(101)
        ]
    return rows


def packets(name: str) -> dict[str, Any]:
    rows = fixture_rows(name)
    return {
        table: {
            "columns": [
                {"name": column, "type": field_type, "nullable": column == "source_ref"}
                for column, field_type in columns
            ],
            "rows": rows[table],
        }
        for table, columns in SCHEMAS.items()
    }


def seed_postgres(port: int) -> dict[str, Any]:
    convert: dict[str, Callable[[str], object]] = {
        "instant": datetime.fromisoformat,
        "decimal": Decimal,
        "date": date.fromisoformat,
    }
    with source_connection(port, owner=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('server_version_num'), current_setting('TimeZone')")
        version = cursor.fetchone()
        if version != ("170009", "UTC"):
            raise EngineError(f"Expected PostgreSQL 17.9 on UTC. Received {version!r}.")
        cursor.execute("""DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migration_reader') THEN
            CREATE ROLE migration_reader LOGIN PASSWORD 'local-reader-only' NOINHERIT;
          END IF;
        END $$""")
        cursor.execute("REVOKE ALL ON DATABASE migration FROM PUBLIC")
        cursor.execute("GRANT CONNECT ON DATABASE migration TO migration_reader")
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        cursor.execute("ALTER ROLE migration_reader SET default_transaction_read_only = on")
        cursor.execute("ALTER ROLE migration_reader SET statement_timeout = '5s'")
        cursor.execute("ALTER ROLE migration_reader SET timezone = 'UTC'")
        for fixture_id in (*FIXTURES, "row_limit"):
            schema = sql.Identifier(f"fixture_{fixture_id}")
            cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO migration_reader").format(schema))
            rows = fixture_rows(fixture_id)
            for table, columns in SCHEMAS.items():
                full_name = sql.Identifier(f"fixture_{fixture_id}", table)
                fields = []
                for index, (name, kind) in enumerate(columns):
                    constraint = (
                        " PRIMARY KEY"
                        if index == 0
                        else ""
                        if name == "source_ref"
                        else " NOT NULL"
                    )
                    fields.append(
                        sql.SQL("{} {}{}").format(
                            sql.Identifier(name), sql.SQL(PG_TYPES[kind]), sql.SQL(constraint)
                        )
                    )
                cursor.execute(
                    sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                        full_name, sql.SQL(", ").join(fields)
                    )
                )
                cursor.execute(sql.SQL("TRUNCATE {}").format(full_name))
                encoded = [
                    tuple(
                        convert[kind](value) if value is not None and kind in convert else value
                        for value, (_, kind) in zip(record, columns, strict=True)
                    )
                    for record in rows[table]
                ]
                cursor.executemany(
                    sql.SQL("INSERT INTO {} VALUES ({})").format(
                        full_name, sql.SQL(", ").join([sql.Placeholder()] * len(columns))
                    ),
                    encoded,
                )
                cursor.execute(sql.SQL("GRANT SELECT ON {} TO migration_reader").format(full_name))
    return {
        "postgres": "17.9",
        "timezone": "UTC",
        "fixtures": {
            fixture_id: {
                "digest": digest(packets(fixture_id)),
                "counts": {name: len(rows) for name, rows in fixture_rows(fixture_id).items()},
            }
            for fixture_id in (*FIXTURES, "row_limit")
        },
    }
