"""Read typed fixture records and return bounded results over a JSON Lines protocol."""

import json
import os
import re
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from py4j.protocol import Py4JJavaError
from pyspark.errors import PySparkException
from pyspark.sql import SparkSession
from pyspark.sql.types import (
    BooleanType,
    DateType,
    DecimalType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

VERSION = 1


def load_rows(session: SparkSession, fixture: dict[str, Any]) -> None:
    # Build the Spark schemas from the packet and decode typed JSON values.
    data_types = {
        "text": StringType(),
        "integer": LongType(),
        "boolean": BooleanType(),
        "decimal": DecimalType(18, 2),
        "date": DateType(),
        "instant": TimestampType(),
    }
    decode = {"decimal": Decimal, "date": date.fromisoformat, "instant": datetime.fromisoformat}
    for table, source in fixture.items():
        columns = source["columns"]
        schema = StructType(
            [
                StructField(column["name"], data_types[column["type"]], column["nullable"])
                for column in columns
            ]
        )
        records = [
            tuple(
                decode[column["type"]](value)
                if value is not None and column["type"] in decode
                else value
                for value, column in zip(row, columns, strict=True)
            )
            for row in source["rows"]
        ]
        session.createDataFrame(records, schema).createOrReplaceTempView(table)


def encode(value: Any) -> Any:
    # Send timestamps, dates and decimals as strings in the result record.
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def issue(error: Exception) -> str:
    # Return a Spark condition or exception class name from the runtime error.
    if isinstance(error, PySparkException):
        condition = error.getCondition() or "SPARK_ERROR"
        if re.fullmatch(r"[A-Z0-9_.]{1,80}", condition):
            return condition
    if isinstance(error, Py4JJavaError):
        name = error.java_exception.getClass().getSimpleName()
        if re.fullmatch(r"[A-Za-z0-9_]{1,80}", name):
            return name
    return type(error).__name__


def main() -> None:
    # Reserve the original stdout for records. JVM and Python diagnostics go to stderr.
    with os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1) as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        spark = (
            SparkSession.builder.master("local[2]")
            .appName("sql-migration-verifier")
            .config("spark.ui.enabled", "false")
            .config("spark.ui.showConsoleProgress", "false")
            .config("spark.sql.shuffle.partitions", "2")
            .config("spark.sql.adaptive.enabled", "false")
            .config("spark.rpc.io.serverThreads", "2")
            .config("spark.rpc.io.clientThreads", "2")
            .config("spark.sql.ansi.enabled", "true")
            .config("spark.sql.timestampType", "TIMESTAMP_LTZ")
            .config("spark.sql.session.timeZone", "UTC")
            .config("spark.sql.warehouse.dir", "/tmp/warehouse")
            .getOrCreate()
        )
        spark.sparkContext.setLogLevel("ERROR")
        try:
            if spark.version != "4.0.1":
                raise RuntimeError(f"Expected Spark 4.0.1. Found {spark.version}.")
            # Send the version and timezone in the startup record.
            print(
                json.dumps(
                    {
                        "version": VERSION,
                        "status": "READY",
                        "spark": spark.version,
                        "java": spark.sparkContext._jvm.java.lang.System.getProperty(
                            "java.version"
                        ),
                        "timezone": "UTC",
                    }
                ),
                file=protocol,
                flush=True,
            )
            for line in sys.stdin:
                request = json.loads(line)
                if request["version"] != VERSION:
                    raise ValueError("Unsupported worker protocol version.")
                try:
                    # Load temporary views and execute the request inside a fresh SQL session.
                    active = spark.newSession()
                    load_rows(active, request["fixture"])
                    result = active.sql(request["query"])
                    # Set ROW_LIMIT if the query returns more rows than the request allows.
                    rows = result.take(request["cap"] + 1)
                    response = (
                        {"status": "ROW_LIMIT"}
                        if len(rows) > request["cap"]
                        else {
                            "status": "OK",
                            "columns": [
                                {"name": field.name, "type": field.dataType.simpleString()}
                                for field in result.schema.fields
                            ],
                            "rows": [[encode(value) for value in row] for row in rows],
                        }
                    )
                except Exception as error:
                    response = {"status": "QUERY_ERROR", "error": issue(error)}
                print(
                    json.dumps({"version": VERSION, **response}, allow_nan=False),
                    file=protocol,
                    flush=True,
                )
        finally:
            spark.stop()


if __name__ == "__main__":
    main()
