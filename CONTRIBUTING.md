# Contributing

Contributions should make an industry workflow easier to reproduce or expose a meaningful integration failure.

## Set up

Use Python 3.12, Git and uv. Run `uv sync --locked --all-extras`, then `uv run pytest` and `uv run ruff check .`. Node examples use `cd typescript && npm ci --ignore-scripts && npm test && npm run typecheck`. Use `npm run format` to format the TypeScript and dashboard sources; CI enforces `npm run format:check`.

## Add a scenario

1. State the protocol, dependency versions, workload and expected observable result.
2. Reuse an actual maintained industry tool where practical. Document its source, license and supported runtime. Keep heavyweight stacks optional.
3. Use deterministic seeds and synthetic identifiers. Include a successful path, an adversarial counterexample and explicit failure handling.
4. Return the common suite/check/metrics structure. Missing prerequisites must be BLOCKED; test-runner exceptions must be ERROR. A failing product expectation stays FAIL.
5. Add bounded execution, cleanup and tests for the observable contract. Include actual telemetry where visualization helps interpretation.
6. Document exactly which systems execute and which external behavior is simulated. Keep physical-world, protocol, SDK and model-effectiveness results distinct.
7. Run the clean installation and relevant tests. Include the commands and evidence in the pull request.

Never add credentials, real customer data, generated vault tickets, private URLs, payment sessions or private local paths. Use environment variables for live credentials. Do not run live service tests from untrusted pull-request CI.

## Pull requests

Describe the user-visible behavior, dependency/licensing changes, tests, platform coverage and remaining limitations. Keep changes focused. A regression fix should include a meaningful reproducer.

Update the lockfile with `uv lock` after dependency changes. Commit `uv.lock` and `typescript/package-lock.json`. CI uses the frozen lockfiles. Dependency updates should be tested, including the platform-specific MuJoCo constraint and any source-build requirements on Intel macOS.

## Issue reports

Provide a synthetic reproducer, lab/SDK/tool versions, operating system, command, expected result and sanitized JSON output. Report security vulnerabilities privately according to [SECURITY.md](SECURITY.md).
