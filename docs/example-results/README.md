# Example results

The [Linux CI run](https://github.com/Vox-Void/sql-migration-verifier/actions/runs/44918092338) built both engines, ran the reference and challenge tests, produced the demo and checked two matching benchmark runs. See the [demo evidence](demo-results.json), [benchmark summary](eval-summary.json) and [per-case results](eval-results.jsonl).

## Offline benchmark

| Mode | Verified | Mismatch | Review required | Error |
| --- | ---: | ---: | ---: | ---: |
| Strict SQLGlot baseline | 12/12 | 0 | 0 | 0 |
| Recorded repair arm | 12/12 | 0 | 0 | 0 |

The baseline passed every scored case, so the recorded arm had no failed translation to repair. The separate demo starts from a deliberately incorrect UTC-day candidate. The committed local-day repair passes the public fixture and both additional fixtures with the same SQL hash. The offline evaluation used no provider requests.

All eight known-bad candidates received their expected decision, with zero false approvals. Two runs produced the same content digest. The digest excludes elapsed times and run metadata and includes verdicts, fixture and SQL hashes, and the detailed differences.

## What the negative cases show

- The multiplicity case returns the same three rows by distinct membership and the same ZAR 300.30 total. The complete multiset comparison finds one missing `A` and one extra `B`.
- The refund fan-out case shows an incorrect amount for the same capture key. A join can run and still change the metric.
- The review case stops before Spark because the candidate omits the capture ID tie-break. The target execution count stays at zero.
- The row-budget case has 101 source rows. The Spark cross join exceeds the 10,000-row cap and returns an incomplete-result decision with no partial values.
- The shared-error case catches an intentionally wrong PostgreSQL result against the oracle. Spark is never called for that case.

## Problems found during testing

- The Spark worker passed its first queries but began returning execution errors in the full suite. The worker now uses smaller RPC thread pools, disables adaptive SQL execution and has headroom below an explicit container process cap. The integration suite runs repeated queries within a single worker.
- The first merchant-local report grouped by merchant and day. That would add amounts across currencies. The report now groups by currency and the Spark regression checks the separate ZAR and USD rows for the same local day.
