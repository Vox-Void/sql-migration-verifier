# SQL Migration Verifier

Verify that a PostgreSQL reporting query and its Spark SQL candidate agree with the business rules for synthetic payments. Verification covers the declared fixtures and engine settings.

Watch the [2:39 captioned demo](docs/demo.mp4) for the duplicate-row regression, the local-day repair and the case that stops for review.

## Run it

Use Bash on Linux or WSL2 with Docker, Docker Compose v2 and `uv` 0.13.9. Run these commands from the repository root.

```bash
./scripts/dev bootstrap
./scripts/dev test
./scripts/dev demo
./scripts/dev eval
```

Bootstrap downloads the locked dependencies and builds the Spark worker image. It starts a local PostgreSQL instance and seeds it with fictional payment records. The next three commands require no API key.

The command prints the actual engine versions and writes fixture checksums and row counts to `artifacts/fixture-digest.json`. The PostgreSQL port is bound to loopback. The Spark process runs in a container without a network connection.

## What the demo shows

Open `artifacts/demo/index.html` locally. The walkthrough includes four checks.

| Case | What to look for |
| --- | --- |
| `Q01` | A translated capture report agrees with the independently calculated totals on all three fixtures. |
| `C01` | The target returns three rows, the same distinct rows and the same ZAR 300.30 total. The bag comparison finds one missing `A` and one extra `B`. |
| `C05` | A query without its capture ID tie-break is sent for human review before Spark executes it. |
| `Q03` repair | A fixed Spark SQL candidate applies the merchant's time zone before grouping. Its hash stays the same across both held-out fixtures. |

The evaluator writes `artifacts/eval/results.jsonl`, `summary.json` and `report.md`. Reviewers can inspect exact verdicts, expected and actual row differences, SQL hashes and the diff. See the [verification pull request](https://github.com/Vox-Void/sql-migration-verifier/pull/1) for the multiplicity regression.

## How a verdict is reached

1. Parse one PostgreSQL query and check the SQL forms and relations it uses. Execute it through the restricted source role and compare it with an independent expected result. If the source is wrong, stop the case before running Spark.
2. Translate the source with SQLGlot. Check each candidate against the same read-only policy, including a recorded or live repair. Run it against the repair fixture in an isolated Spark worker.
3. Freeze the SQL hash when the repair fixture passes. Run the same candidate on the two additional fixtures. The comparator checks typed keys, full row multiplicity or exact order, as declared by the case contract.
4. Publish an evidence packet. A human reviewer decides whether to use the SQL outside the fixture environment.

The [reviewer guide](docs/runbook.md) covers exit codes, scoring and live mode. The [architecture notes](docs/architecture.md) explain the isolation and assumptions. The [example results](docs/example-results/README.md) include the benchmarks and problems found during testing.
