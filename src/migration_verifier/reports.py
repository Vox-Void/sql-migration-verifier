"""Create machine-readable evidence and a static reviewer view."""

import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from migration_verifier.engines import ROOT
from migration_verifier.fixtures import digest
from migration_verifier.policy import CASE_IDS, load_case
from migration_verifier.verify import Runtime, provenance, verify


def challenge(runtime: Runtime, challenge_id: str) -> dict[str, Any]:
    spec = json.loads((ROOT / "challenges" / f"{challenge_id}.json").read_bytes())
    outcome = verify(
        runtime,
        load_case(spec["case_id"]),
        "baseline",
        fixture_ids=(spec["fixture"],),
        initial_sql=spec["candidate"],
        source_sql=spec.get("source"),
    )
    return {
        "challenge_id": challenge_id,
        "expected_verdict": spec["expected"],
        "fault": spec["purpose"],
        **outcome,
    }


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def stable_record(row: dict[str, Any]) -> str:
    content = {key: value for key, value in row.items() if key != "target_attempts"}
    content["target_attempts"] = [
        {key: value for key, value in attempt.items() if key != "duration_ms"}
        for attempt in row["target_attempts"]
    ]
    return digest(content)


def evaluation(runtime: Runtime, output: Path, mode: str) -> bool:
    output.mkdir(parents=True, exist_ok=True)
    rows = [
        verify(runtime, load_case(case), arm) for arm in ("baseline", mode) for case in CASE_IDS
    ]
    challenges = [challenge(runtime, f"C{index:02}") for index in range(1, 9)]
    scores = {}
    for arm in ("baseline", mode):
        cases = [row for row in rows if row["mode"] == arm]
        scores[arm] = {
            "cases": len(cases),
            "verified": sum(row["verdict"] == "VERIFIED" for row in cases),
            "mismatch": sum(row["verdict"] == "MISMATCH" for row in cases),
            "review_required": sum(row["verdict"] == "REVIEW_REQUIRED" for row in cases),
            "error": sum(row["verdict"] == "ERROR" for row in cases),
        }
    baseline_failures = {
        row["case_id"] for row in rows if row["mode"] == "baseline" and row["verdict"] != "VERIFIED"
    }
    recovered = [
        row["case_id"]
        for row in rows
        if row["mode"] == mode
        and row["case_id"] in baseline_failures
        and row["verdict"] == "VERIFIED"
    ]
    safe = all(row["verdict"] == row["expected_verdict"] for row in challenges)
    metadata = provenance()
    with (output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows + challenges:
            stream.write(
                json.dumps({**row, "engine": metadata, "content_hash": stable_record(row)}) + "\n"
            )
    report = {
        "version": 1,
        "run_at": datetime.now(UTC).isoformat(),
        "engines": metadata,
        "arms": scores,
        "recovered": recovered,
        "baseline_non_passes": len(baseline_failures),
        "challenge_count": len(challenges),
        "false_approvals": sum(row["verdict"] == "VERIFIED" for row in challenges),
        "content_digest": digest([stable_record(row) for row in rows + challenges]),
    }
    write_json(output / "summary.json", report)
    lines = [
        "# Migration evaluation",
        "",
        "| Mode | Verified | Mismatch | Review required | Error |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {arm} | {info['verified']}/{info['cases']} | {info['mismatch']} | {info['review_required']} | {info['error']} |"
        for arm, info in scores.items()
    )
    lines.extend(
        [
            "",
            f"False approvals in known-bad challenges: {report['false_approvals']}/{len(challenges)}.",
            "",
            "Recorded repair inputs run through both engines. Provider usage is null for recorded attempts.",
            "",
        ]
    )
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return safe and all(info["error"] == 0 for info in scores.values())


def demo(runtime: Runtime, output: Path) -> bool:
    verified = verify(runtime, load_case("Q01"), "baseline")
    multiplicity = challenge(runtime, "C01")
    escalation = challenge(runtime, "C05")
    drift = challenge(runtime, "C03")
    repair = verify(runtime, load_case("Q03"), "recorded", initial_sql=drift["candidate_sql"])
    sections = [
        ("Verified capture report", verified),
        ("The same totals hide a duplicate-row error", multiplicity),
        ("A missing tie-break needs review", escalation),
        ("Replay a local-day SQL repair", repair),
    ]
    output.mkdir(parents=True, exist_ok=True)
    html = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>",
        "<title>SQL Migration Verifier</title><style>body{margin:0 auto;max-width:68rem;padding:2rem;font:1rem system-ui;color:#17212b;background:#f3f7fa}section{background:white;border-radius:.7rem;padding:1.3rem;margin:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef3f7;padding:1rem}h1,h2{color:#183958}code{font-family:monospace}</style>",
        "<main><h1>SQL Migration Verifier</h1><p>Synthetic payments results from PostgreSQL and Spark. Review the SQL, fixture scope and differences before adopting a candidate.</p>",
    ]
    for title, row in sections:
        html.extend(
            [
                f"<section><h2>{escape(title)}</h2>",
                f"<p>{escape(row['purpose'])}. Verdict <strong>{escape(row['verdict'])}</strong>.</p>",
                f"<details open><summary>Target checks</summary><pre>{escape(json.dumps(row['target_attempts'], indent=2))}</pre></details>",
                f"<details><summary>SQL difference</summary><pre>{escape(row['sql_diff'])}</pre></details></section>",
            ]
        )
        print(f"{row['case_id']} {row['verdict']} {title}")
    html.append("</main></html>")
    (output / "index.html").write_text("\n".join(html) + "\n", encoding="utf-8")
    write_json(
        output / "results.json", {"engines": provenance(), "cases": [row for _, row in sections]}
    )
    print(f"Report {output / 'index.html'}")
    return [row["verdict"] for _, row in sections] == [
        "VERIFIED",
        "MISMATCH",
        "REVIEW_REQUIRED",
        "VERIFIED",
    ]
