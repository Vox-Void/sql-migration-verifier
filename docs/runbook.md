# Reviewer guide

## Review one case

After bootstrap, run the verifier with a named case and a result file.

```bash
./scripts/dev verify --case Q03 --mode baseline --out artifacts/q03.json
```

Read the source comparison first. Check the fixture hashes, output types and comparison mode. Review the candidate SQL and its diff. Use the attempt records to see how far an unsupported or blocked candidate progressed.

| Exit | Verdict | Meaning |
| --- | --- | --- |
| 0 | `VERIFIED` | Source and target match all named independent expectations. |
| 2 | `MISMATCH` | A completed target check returned different results. |
| 3 | `REVIEW_REQUIRED` | The verifier could not finish a semantic comparison. Inspect the policy, runtime or row-limit reason. |
| 4 | `ERROR` | The input contract, source oracle, or environment failed. |

For ordered reports, check the final tie-break and verify that it represents the business rule. For financial reports, check row grain and currency before comparing totals. A row count and total can agree when different rows occur multiple times.

## Benchmark modes

- `./scripts/dev eval` compares strict SQLGlot translation with the recorded-repair arm. It runs real PostgreSQL and Spark checks in both arms. A recorded proposal is considered only if the first candidate fails its repair fixture. Provider usage stays null in the recorded arm.
- `./scripts/dev eval --mode live` enables optional OpenAI repair. Set `OPENAI_API_KEY` in the host environment and set `MIGRATION_MODEL` to an available Responses API model. The default is `gpt-6.2-sol`. A case may request up to two proposals after a baseline failure. Each request has a 12-second transport timeout and a 1,100-token output budget.
- For escalation, review the first blocking reason. An unsupported translation, query failure or result over the cap belongs in review. A source-oracle error calls for checking the source report and the fixture expectations before retrying a migration.

## Inspect fixtures

Each case declares three fixture IDs. `repair` is the example available to the repair adapter. `late` adds a microsecond boundary and a later cutoff. `ties` adds same-time events and another uncaptured authorisation. The [known-bad challenges](../challenges) cover permission, comparison and execution failures. The `row_limit` fixture has 101 input rows to exercise a complete source read and an oversized target cross join.
