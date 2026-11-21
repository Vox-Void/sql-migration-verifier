"""Run the local SQL migration verifier."""

import argparse
import json
import os
from pathlib import Path

from migration_verifier.engines import EngineError, SparkWorker, postgres_port
from migration_verifier.fixtures import packets, seed_postgres
from migration_verifier.policy import CASE_IDS, load_case
from migration_verifier.reports import demo, evaluation, write_json
from migration_verifier.verify import EXIT_CODES, Runtime, provenance, verify


def bootstrap() -> int:
    metadata = seed_postgres(postgres_port())
    with SparkWorker() as worker:
        baseline = worker.query(
            "SELECT COUNT(*) AS authorisations FROM authorisations", packets("repair"), 10
        )
        if baseline.get("rows") != [[7]]:
            raise EngineError("Spark fixture smoke check returned the wrong count.")
        metadata["spark"] = worker.info
    output = Path("artifacts/fixture-digest.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), **metadata}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap")
    demo_parser = commands.add_parser("demo")
    demo_parser.add_argument("--out", type=Path, default=Path("artifacts/demo"))
    eval_parser = commands.add_parser("eval")
    eval_parser.add_argument("--out", type=Path, default=Path("artifacts/eval"))
    eval_parser.add_argument("--mode", choices=("recorded", "live"), default="recorded")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--case", choices=CASE_IDS, required=True)
    verify_parser.add_argument(
        "--mode", choices=("baseline", "recorded", "live"), default="baseline"
    )
    verify_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if getattr(args, "mode", None) == "live" and not os.environ.get("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY to opt into live repair.")
    try:
        if args.command == "bootstrap":
            return bootstrap()
        with Runtime() as runtime:
            if args.command == "demo":
                return 0 if demo(runtime, args.out) else 1
            if args.command == "eval":
                return 0 if evaluation(runtime, args.out, args.mode) else 1
            outcome = verify(runtime, load_case(args.case), args.mode)
            write_json(args.out, {"engines": provenance(), **outcome})
            print(f"{outcome['case_id']} {outcome['verdict']} {outcome['reason_code']}. {args.out}")
            return EXIT_CODES[outcome["verdict"]]
    except EngineError as error:
        parser.exit(4, f"Runtime check failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
