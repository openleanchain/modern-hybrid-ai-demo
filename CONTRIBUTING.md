# Contributing

Thank you for improving this reference implementation.

1. Create a focused branch from the default branch.
2. Keep mock mode deterministic and offline.
3. Add or update an eval for behavior changes.
4. Run `python -m evals.run_evals` with `python -m llm_service.app_llm` running.
5. Do not commit databases, API keys, customer data, logs, or generated runtime files.
6. Describe the architectural behavior and verification in the pull request.

Please keep changes scoped. This project favors explicit enterprise controls and understandable flows over framework-heavy abstractions.

