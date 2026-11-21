# How the SQL Migration Verifier works

This project checks reporting SQL moved from PostgreSQL to Spark. It runs both queries on synthetic payment data and checks each result against expected rows. For example, a report that groups by UTC date can look right until a Johannesburg merchant makes a capture just after local midnight.

The output shows which fixtures passed, where the rows differed and what SQL the verifier tried. A reviewer uses that evidence to decide whether to adopt the query.

## How it was built

1. **The cases and expected answers came first.** The twelve cases cover issues such as refund fan-out, duplicate rows, nulls and time zones. Each case directory contains a PostgreSQL query, a reviewed Spark query and a contract. The contract lists the allowed tables and functions, the expected columns and types, and how to compare the rows.
2. **Both engines got their own fixture loader.** PostgreSQL uses a separate schema for each fixture. Spark gets typed records from the same fixture bundle and loads them into temporary views. Both use pinned versions. Each loader preserves types such as exact decimals and timestamps so the comparison can detect a change in meaning.
3. **Source checks came before translation.** SQLGlot parses the source and renders it as Spark SQL. The verifier compares PostgreSQL with an independent answer before it runs a target query. If both engines make the same mistake, comparing them with one another would miss it.
4. **The repair step uses the same checks as the first attempt.** Once a candidate passes the repair fixture, that exact SQL runs on the other fixtures. The demo uses a saved repair, so it can show this whole path without an API key.

## Follow a local-day report through the code

### Load the case

Case `Q03` totals captured payments by merchant, currency and merchant-local date. The first version grouped by merchant and date, which allowed ZAR and USD to be added together. The contract now includes currency in the row key, and the regression checks that both currencies appear separately for the same merchant and day.

- [Q03 case directory](../cases/Q03)
- [Fixture records and schema](../src/migration_verifier/fixtures.py)

### Check the expected rows

`verify()` first checks that the source query stays within the case contract. It then runs the query against PostgreSQL with the read-only fixture role. On the first fixture, it uses the committed expected rows. On `late` and `ties`, a small Python calculation derives the rows from the underlying data. For `Q03`, that calculation converts each capture time to the merchant's local time before grouping. A disagreement at this stage stops the run with `ERROR`.

- [Verification flow](../src/migration_verifier/verify.py)
- [First fixture's expected rows](../fixtures/expected-repair.json)
- [Python reference calculations](../src/migration_verifier/oracle.py)

### Run the candidate in Spark

The baseline run translates the source query. The demo supplies a deliberately wrong Spark version that groups by UTC date. Each candidate must use the tables and functions allowed by its contract. Once it passes that check, the driver sends the SQL, fixture records and row cap to the isolated Spark worker as one JSON line.

Spark loads the fixture in a fresh SQL session and sends back column types and rows. It fetches one row beyond the cap to detect an oversized result. If it crosses the cap, it returns `ROW_LIMIT` without sending partial rows. The verifier treats that outcome as `REVIEW_REQUIRED`.

- [SQL checks and translation](../src/migration_verifier/policy.py)
- [Engine connections and worker protocol](../src/migration_verifier/engines.py)
- [Spark worker](../src/spark_worker/worker.py)

### Replay the fix and read the result

In the demo, the UTC candidate puts ZAR 70.00 on 7 January for merchant `M1`. The expected total is ZAR 40.00. The other ZAR 30.00 belongs to 8 January in Johannesburg.

The saved suggestion applies the merchant's time zone before taking the date, while preserving the merchant and currency grouping. The verifier checks the suggestion on `repair`, then runs the same SQL on `late` and `ties`. The report shows a first attempt marked `MISMATCH` followed by three passing attempts. All three passing attempts share the same SQL hash.

Look at `source_checks` for the PostgreSQL results, `target_attempts` for each Spark check and `sql_diff` for the proposed change. The verdict for the demo case is `VERIFIED` within those fixtures and engine settings.

- [Saved Q03 repair](../repairs/Q03.json)
- [Repair adapter](../src/migration_verifier/repair.py)
- [Demo report](example-results/demo-results.json)

## How the row comparison catches mistakes

The comparator checks column names, column order and logical types before looking at values. A keyed case matches rows by its key and reports missing keys, extra keys and changed values. An ordered case also checks row positions. A bag case counts how often every complete row appears.

In challenge `C01`, the expected references are `A, A, B` and the candidate returns `A, B, B`. Each row is worth ZAR 100.10, so both results contain three rows and total ZAR 300.30. Counting duplicates exposes one missing `A` and one extra `B`. The report marks the case `MISMATCH`.

- [Comparator](../src/migration_verifier/compare.py)
- [Duplicate-row challenge](../challenges/C01.json)

## Try it yourself

After bootstrap, run one baseline case and open its JSON report. To see the recorded repair, run the demo and open its HTML report.

```bash
./scripts/dev verify --case Q03 --mode baseline --out artifacts/q03.json
./scripts/dev demo
```

The [reviewer runbook](runbook.md) lists exit codes and benchmark commands. The [architecture notes](architecture.md) describe the runtime isolation. The [example results](example-results/README.md) include the CI evidence and the issues found during testing.
