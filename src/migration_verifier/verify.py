"""Drive the source oracle, target gate and review verdict."""

import json
import os
import subprocess
from dataclasses import asdict
from difflib import unified_diff
from time import monotonic
from typing import Any

import psycopg

from migration_verifier.compare import Result, compare
from migration_verifier.engines import (
    ROOT,
    EngineError,
    SparkWorker,
    postgres_port,
    postgres_query,
    spark_result,
)
from migration_verifier.fixtures import SCHEMAS, check_manifest, digest, packets
from migration_verifier.oracle import expected
from migration_verifier.policy import Case, PolicyError, gate, translate
from migration_verifier.repair import PROMPT_VERSION, propose

EXIT_CODES = {"VERIFIED": 0, "MISMATCH": 2, "REVIEW_REQUIRED": 3, "ERROR": 4}


class Runtime:
    def __init__(self) -> None:
        check_manifest()
        self.port = postgres_port()
        self.worker: SparkWorker | None = None
        self.executions = 0

    def __enter__(self) -> "Runtime":
        return self

    def __exit__(self, *error: object) -> None:
        if self.worker:
            self.worker.close()

    def target(self, sql: str, fixture_id: str, cap: int) -> dict[str, Any]:
        if self.worker is None or self.worker.process is None:
            self.worker = SparkWorker()
            self.worker.__enter__()
        self.executions += 1
        return self.worker.query(sql, packets(fixture_id), cap)


def provenance() -> dict[str, Any]:
    git = ["git", "-C", str(ROOT)]
    commit = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    status = subprocess.run(
        [*git, "status", "--porcelain"], capture_output=True, text=True, timeout=5
    )
    postgres = (
        (ROOT / "compose.yaml")
        .read_text(encoding="utf-8")
        .split("image: postgres:", 1)[1]
        .split()[0]
    )
    images = [
        line.split()[1]
        for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.startswith("FROM ")
    ]
    return {
        "postgres": "17.9",
        "spark": "4.0.1",
        "psycopg": psycopg.__version__,
        "java": "17.0.18",
        "timezone": "UTC",
        "spark_ansi": True,
        "spark_timestamp_type": "TIMESTAMP_LTZ",
        "postgres_image": postgres,
        "worker_base_images": images,
        "revision": commit.stdout.strip() or None,
        "dirty": bool(status.stdout.strip()),
    }


def source_check(
    runtime: Runtime, case: Case, fixture_id: str, sql: str
) -> tuple[Result, dict[str, Any]]:
    oracle = Result(case.contract.shape, expected(case.case_id, fixture_id))
    actual = postgres_query(runtime.port, fixture_id, sql, case.row_cap)
    return oracle, compare(oracle, actual, case.contract)


def check_target(
    runtime: Runtime, case: Case, fixture_id: str, sql: str, oracle: Result
) -> dict[str, Any]:
    start = monotonic()
    document: dict[str, Any] = {"fixture": fixture_id, "sql_hash": digest(sql)}
    try:
        checked = gate(sql, case, "spark")
        document["policy"] = "ALLOWED"
    except PolicyError as error:
        return {**document, "policy": "BLOCKED", "verdict": "REVIEW_REQUIRED", "reason": str(error)}
    try:
        result = runtime.target(checked.sql(dialect="spark"), fixture_id, case.row_cap)
    except (EngineError, OSError, subprocess.SubprocessError) as error:
        return {
            **document,
            "verdict": "REVIEW_REQUIRED",
            "reason": "WORKER_UNAVAILABLE",
            "detail": type(error).__name__,
        }
    document["duration_ms"] = round((monotonic() - start) * 1000)
    if result["status"] != "OK":
        return {
            **document,
            "verdict": "REVIEW_REQUIRED",
            "reason": result["status"],
            "detail": result.get("error"),
            "complete": False,
        }
    diff = compare(oracle, spark_result(result), case.contract)
    return {
        **document,
        "verdict": "VERIFIED" if diff["matched"] else "MISMATCH",
        "reason": "MATCHED_ORACLE" if diff["matched"] else "TARGET_ORACLE_DIFF",
        "comparison": diff,
        "complete": True,
    }


def verify(
    runtime: Runtime,
    case: Case,
    mode: str,
    *,
    fixture_ids: tuple[str, ...] | None = None,
    initial_sql: str | None = None,
    source_sql: str | None = None,
) -> dict[str, Any]:
    requested = case.fixtures if fixture_ids is None else fixture_ids
    repair_fixture = requested[0]
    targets: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    prompts: list[dict[str, Any]] = []
    baseline = None
    final = None
    executions = runtime.executions
    source = case.source if source_sql is None else source_sql

    def result(verdict: str, reason: str) -> dict[str, Any]:
        failed_fixtures = {target["fixture"] for target in targets if target.get("complete")}
        return {
            "version": 1,
            "case_id": case.case_id,
            "purpose": case.purpose,
            "mode": mode,
            "verdict": verdict,
            "reason_code": reason,
            "contract_hash": digest(json.loads(case.contract_bytes)),
            "fixture_hashes": {fixture: digest(packets(fixture)) for fixture in requested},
            "source_sql_hash": digest(source),
            "baseline_sql_hash": digest(baseline) if baseline else None,
            "final_sql_hash": digest(final) if final else None,
            "source_checks": sources,
            "target_attempts": targets,
            "target_executions": runtime.executions - executions,
            "skipped": {
                fixture: "No complete target result"
                for fixture in requested
                if fixture not in failed_fixtures
            },
            "repairs": prompts,
            "prompt_version": PROMPT_VERSION,
            "model": os.environ.get("MIGRATION_MODEL", "gpt-6.2-sol") if mode == "live" else None,
            "candidate_sql": final or baseline,
            "sql_diff": "".join(
                unified_diff(
                    source.splitlines(True),
                    (final or baseline or "").splitlines(True),
                    "postgres",
                    "spark",
                )
            ),
        }

    try:
        parsed = gate(source, case, "postgres")
        oracle, comparison = source_check(runtime, case, repair_fixture, source)
        sources[repair_fixture] = comparison
    except (PolicyError, psycopg.Error, EngineError, ValueError) as error:
        return result("ERROR", f"SOURCE_CHECK_{type(error).__name__}")
    if not comparison["matched"]:
        return result("ERROR", "SOURCE_ORACLE_DIFF")

    try:
        baseline = initial_sql if initial_sql is not None else translate(parsed)
        targets.append(check_target(runtime, case, repair_fixture, baseline, oracle))
    except PolicyError as error:
        targets.append(
            {"fixture": repair_fixture, "verdict": "REVIEW_REQUIRED", "reason": str(error)}
        )
    if targets[-1]["verdict"] == "VERIFIED":
        final = baseline
    current_sql = baseline
    for attempt in range(1, 3) if mode in {"recorded", "live"} and final is None else ():
        # The repair endpoint is called before loading either held-out fixture.
        context = {
            "source_sql": source,
            "purpose": case.purpose,
            "output": case.contract.shape,
            "catalog": {name: SCHEMAS[name] for name in case.relations},
            "candidate_sql": current_sql,
            "diagnostics": targets[-1],
        }
        suggestion = propose(case.case_id, context, mode, attempt)
        prompts.append(asdict(suggestion))
        if suggestion.candidate_sql is None:
            break
        current_sql = suggestion.candidate_sql
        targets.append(check_target(runtime, case, repair_fixture, current_sql, oracle))
        if targets[-1]["verdict"] == "VERIFIED":
            final = current_sql
            break
    if final is None:
        mismatch = next(
            (target for target in reversed(targets) if target["verdict"] == "MISMATCH"), None
        )
        outcome = mismatch or targets[-1]
        return result(outcome["verdict"], outcome["reason"])

    for fixture_id in requested[1:]:
        try:
            oracle, comparison = source_check(runtime, case, fixture_id, source)
        except (psycopg.Error, EngineError, ValueError) as error:
            return result("ERROR", f"SOURCE_CHECK_{type(error).__name__}")
        sources[fixture_id] = comparison
        if not comparison["matched"]:
            return result("ERROR", "SOURCE_ORACLE_DIFF")
        targets.append(check_target(runtime, case, fixture_id, final, oracle))
    outcomes = {entry["verdict"] for entry in targets[-len(requested) :]}
    if "MISMATCH" in outcomes:
        return result("MISMATCH", "HELD_OUT_ORACLE_DIFF")
    if "REVIEW_REQUIRED" in outcomes:
        return result("REVIEW_REQUIRED", "HELD_OUT_INCOMPLETE")
    return result("VERIFIED", "MATCHED_ALL_ORACLES")
