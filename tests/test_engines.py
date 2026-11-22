import subprocess
import sys
from io import StringIO
from types import SimpleNamespace

import pytest

from migration_verifier.cli import main
from migration_verifier.engines import EngineError, SparkWorker, postgres_port
from migration_verifier.verify import provenance


def test_cli_reports_missing_docker_as_runtime_error(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["verifier", "verify", "--case", "Q03", "--out", "unused.json"]
    )

    def missing_docker(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", missing_docker)
    with pytest.raises(SystemExit) as outcome:
        main()
    assert outcome.value.code == 4
    assert "Docker is unavailable" in capsys.readouterr().err


def test_invalid_postgres_port_is_a_runtime_error(monkeypatch):
    result = subprocess.CompletedProcess(["docker"], 0, stdout="127.0.0.1:abc\n")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)
    with pytest.raises(EngineError, match="PostgreSQL is unavailable"):
        postgres_port()


def test_spark_worker_rejects_a_non_object_protocol_record():
    worker = SparkWorker()
    worker.records.put("[]")
    with pytest.raises(EngineError, match="invalid record"):
        worker.read(timeout=0)


def test_spark_worker_rejects_missing_startup_version_info(monkeypatch):
    worker = SparkWorker()
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(stdout=StringIO())
    )
    monkeypatch.setattr(worker, "read", lambda timeout: {"version": 1})
    closed = []
    monkeypatch.setattr(worker, "close", lambda: closed.append(True))
    with pytest.raises(EngineError, match="Expected the pinned Spark"):
        worker.__enter__()
    assert closed == [True]


@pytest.mark.parametrize(
    "record, message",
    [
        ('{"version": 1, "status": "OK", "columns": [{"name": "count"}], "rows": [[1]]}', "result"),
        ('{"version": 1, "status": "OK", "columns": [], "rows": [1]}', "result"),
        ('{"version": 1, "status": "READY"}', "status"),
    ],
)
def test_spark_worker_closes_after_invalid_query_reply(record, message, monkeypatch):
    worker = SparkWorker()
    worker.process = SimpleNamespace(stdin=StringIO())
    worker.records.put(record)
    closed = []
    monkeypatch.setattr(worker, "close", lambda: closed.append(True))
    with pytest.raises(EngineError, match=f"invalid query {message}"):
        worker.query("SELECT 1", {}, 1)
    assert closed == [True]


def test_spark_worker_terminates_launcher_when_container_cleanup_times_out(monkeypatch):
    worker = SparkWorker()
    waits = []
    killed = []

    def wait(timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("worker", timeout)
        return 0

    def cleanup(*args, **kwargs):
        raise subprocess.TimeoutExpired("docker", kwargs["timeout"])

    worker.process = SimpleNamespace(
        stdin=StringIO(),
        stdout=StringIO(),
        wait=wait,
        poll=lambda: None,
        kill=lambda: killed.append(True),
    )
    monkeypatch.setattr(subprocess, "run", cleanup)
    worker.close()
    assert worker.process is None
    assert killed == [True]
    assert waits == [5, 5]


def test_provenance_still_reports_engines_without_git(monkeypatch):
    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing_git)
    metadata = provenance()
    assert metadata["spark"] == "4.0.1"
    assert metadata["revision"] is None
    assert metadata["dirty"] is None
