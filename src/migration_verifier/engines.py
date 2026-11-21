"""Control the source connection and the isolated Spark worker."""

import json
import subprocess
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql

from migration_verifier.compare import Result

ROOT = Path(__file__).resolve().parents[2]
VERSION = 1
POSTGRES_TYPES = {
    16: "boolean",
    20: "integer",
    21: "integer",
    23: "integer",
    25: "text",
    1043: "text",
    1082: "date",
    1114: "local_timestamp",
    1184: "instant",
    700: "float",
    701: "double",
}
SPARK_TYPES = {
    "string": "text",
    "bigint": "integer",
    "int": "integer",
    "smallint": "integer",
    "tinyint": "integer",
    "timestamp": "instant",
    "timestamp_ntz": "local_timestamp",
}


class EngineError(RuntimeError):
    pass


def postgres_port() -> int:
    command = subprocess.run(
        ["docker", "compose", "port", "postgres", "5432"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if command.returncode or not command.stdout.strip().startswith("127.0.0.1:"):
        raise EngineError("PostgreSQL is unavailable. Run ./scripts/dev bootstrap.")
    return int(command.stdout.strip().rsplit(":", 1)[1])


def source_connection(port: int, *, owner: bool = False) -> psycopg.Connection[Any]:
    return psycopg.connect(
        host="127.0.0.1",
        port=port,
        dbname="migration",
        connect_timeout=5,
        user="fixture_owner" if owner else "migration_reader",
        password="local-fixture-only" if owner else "local-reader-only",
    )


def postgres_query(port: int, fixture_id: str, query: str, cap: int) -> Result:
    with (
        source_connection(port) as connection,
        connection.cursor(name="migration_result") as cursor,
    ):
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL timezone = 'UTC'")
        connection.execute(
            sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(
                sql.Identifier(f"fixture_{fixture_id}")
            )
        )
        cursor.execute(query)
        rows = cursor.fetchmany(cap + 1)
        if len(rows) > cap:
            raise EngineError("SOURCE_ROW_LIMIT")
        if cursor.description is None:
            raise EngineError("SOURCE_SCHEMA_MISSING")
        columns = tuple(
            (
                column.name,
                f"decimal({column.precision},{column.scale})"
                if column.type_code == 1700
                else POSTGRES_TYPES.get(column.type_code, f"oid:{column.type_code}"),
            )
            for column in cursor.description
        )
        return Result(columns, [list(record) for record in rows])


def spark_result(document: dict[str, Any]) -> Result:
    return Result(
        tuple(
            (column["name"], SPARK_TYPES.get(column["type"], column["type"]))
            for column in document["columns"]
        ),
        document["rows"],
    )


class SparkWorker:
    def __init__(self) -> None:
        self.name = f"migration-worker-{uuid4().hex}"
        self.records: Queue[str] = Queue()
        self.process: subprocess.Popen[str] | None = None
        self.info: dict[str, Any] = {}

    def __enter__(self) -> "SparkWorker":
        self.process = subprocess.Popen(
            ["docker", "compose", "run", "--rm", "-T", "--no-deps", "--name", self.name, "worker"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        stdout = self.process.stdout
        assert stdout is not None

        def receive() -> None:
            for fragment in stdout:
                self.records.put(fragment)
            self.records.put("")

        Thread(target=receive, daemon=True).start()
        try:
            self.info = self.read(60)
            if self.info["status"] != "READY" or self.info["spark"] != "4.0.1":
                raise EngineError("Expected the pinned Spark 4.0.1 worker.")
        except (EngineError, KeyError):
            self.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["docker", "rm", "-f", self.name],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            self.process = None

    def read(self, timeout: float) -> dict[str, Any]:
        try:
            record = self.records.get(timeout=timeout)
        except Empty as error:
            raise EngineError("Spark worker deadline exceeded.") from error
        if not record:
            raise EngineError("Spark worker exited before returning a result.")
        try:
            document: dict[str, Any] = json.loads(record)
        except ValueError as error:
            raise EngineError("Spark worker returned an invalid record.") from error
        if document.get("version") != VERSION:
            raise EngineError("Spark worker protocol version mismatch.")
        return document

    def query(self, query: str, fixture: dict[str, Any], cap: int) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise EngineError("The Spark worker is unavailable.")
        value = {"version": VERSION, "query": query, "fixture": fixture, "cap": cap}
        try:
            self.process.stdin.write(json.dumps(value) + "\n")
            self.process.stdin.flush()
            return self.read(30)
        except (OSError, EngineError):
            self.close()
            raise
