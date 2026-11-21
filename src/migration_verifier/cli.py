"""Run the local SQL migration verifier."""

import argparse
import json
from pathlib import Path

from migration_verifier.engines import EngineError, SparkWorker, postgres_port
from migration_verifier.fixtures import packets, seed_postgres


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bootstrap",))
    parser.parse_args()
    try:
        metadata = seed_postgres(postgres_port())
        with SparkWorker() as worker:
            baseline = worker.query(
                "SELECT COUNT(*) AS authorisations FROM authorisations", packets("repair"), 10
            )
            if baseline.get("rows") != [[7]]:
                raise EngineError("Spark fixture smoke check returned the wrong count.")
            metadata["spark"] = worker.info
    except EngineError as error:
        parser.exit(4, f"Runtime check failed: {error}\n")
    output = Path("artifacts/fixture-digest.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), **metadata}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
