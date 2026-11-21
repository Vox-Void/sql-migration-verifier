# SQL Migration Verifier

Verify that a PostgreSQL reporting query and its Spark SQL candidate agree with the business rules for synthetic payments. Verification covers the declared fixtures and engine settings.

## Bootstrap

Use Bash on Linux or WSL2 with Docker, Docker Compose v2 and `uv` 0.13.9. From the repo root, run `./scripts/dev bootstrap`. The first run downloads the locked dependencies and builds the Spark worker image. It starts a local PostgreSQL instance and seeds it with fictional payment records.

The command prints the actual engine versions and writes fixture checksums and row counts to `artifacts/fixture-digest.json`. The PostgreSQL port is bound to loopback. The Spark process runs in a container without a network connection.
