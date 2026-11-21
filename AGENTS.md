# Repository guide

- Keep fixture data synthetic. Persist no API keys or raw provider responses.
- Keep reviewed SQL references and the held-out fixture answers outside repair inputs.
- Run `./scripts/dev test` and verify the typed row comparisons before updating expected outcomes.
- Preserve the distinct source, policy, execution and comparison verdicts when changing the CLI or reports.
