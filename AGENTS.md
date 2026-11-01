# Working in EigenScope Integration Lab

This is a public developer project for running industry simulations and testing EigenScope integrations. Keep changes useful to a developer arriving without this chat or private project context. Start with [README.md](README.md), [architecture](docs/architecture.md) and the relevant [scenario guide](docs/scenarios/).

## Setup and commands

Use Python 3.12, Git, uv and Node.js 22.18 or later. From the repository root:

```sh
uv sync --locked --all-extras
uv run --frozen eigenscope-lab doctor
uv run --frozen eigenscope-lab list
uv run --frozen eigenscope-lab run all --output results/local.json
uv run --frozen eigenscope-lab serve
```

The dashboard runs at `http://127.0.0.1:8765`. Local scenarios need no account, API key or hosted credits. On this project, Docker is optional. Colima users can select their existing daemon with `docker --context colima`; do not change global Docker settings or restart a shared VM.

For checks relevant to a change:

```sh
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
```

For TypeScript or dashboard assets, run `npm ci --ignore-scripts`, `npm test`, `npm run typecheck` and `npm run format:check` from `typescript/`. `npm run format` formats those sources. Use focused tests during development, then the checks required by the changed interfaces. Distribution changes also require `uv build` and inspection of the packaged assets.

## Code map

- `src/eigenscope_lab/simulators/`: engine adapters and local protocol fixtures.
- `src/eigenscope_lab/fixtures.py` and `live.py`: published inputs and real SDK/API workflows.
- `model_integration.py` and `model_engine.py`: optional automatic-profile lifecycle checks, real causal inference and latency comparisons; see [model integration](docs/model-integration.md).
- `runner.py`, `reporting.py`, `cli.py`: scenario registration, result schema and commands.
- `web.py` and `assets/web/`: loopback HTTP/SSE service and dashboard.
- `tests/` and `typescript/test/`: executable contracts, failure controls and lifecycle checks.
- `infra/telecom/`: optional isolated Open5GS/UERANSIM stack.
- `docs/scenarios/`: setup, units, model assumptions, interface boundaries and sources.

## Simulator correctness

Reuse maintained industry tools and document their sources and licenses. Define units, valid operating ranges, model assumptions and independent expected results before treating a scenario as passing. Check conservation laws, numerical convergence and reference limits where applicable. Keep observed wall time separate from simulation time and added playback delay.

A local engine pass establishes the stated simulation/protocol contract. Evaluate hosted SDK/API protection separately using the actual official client and configured service. A deterministic echo cannot establish model reasoning quality. Preserve failed checks and distinguish missing prerequisites (`BLOCKED`), execution problems (`ERROR`) and failed expectations (`FAIL`). Success requires a nonempty set of strictly true checks.

Use synthetic inputs, bounded workloads and deterministic seeds. Cover a useful positive workflow and meaningful adversarial cases. Validate schemas, finite numbers, units, freshness, authorization and replay at the boundary. Close sockets, subprocesses and temporary workspaces on success, failure and cancellation. For Oil & Gas, model proposals never authorize themselves; independent safety logic and the local operator-approval boundary remain in force.

## UI/UX quality

Give each industry a clear identity, purpose and primary action. Show live measurements and understandable loading, running, stopping, stopped, passed and failed states. Expose the next useful action and keep success styling tied to the current run. Use plain-language recovery in the interface; log detailed exceptions in the local server diagnostics.

Check desktop, tablet and mobile widths, light and dark themes, keyboard navigation, focus, text contrast, long labels and touch targets. Diagrams must reflect actual measurements and state transitions. Avoid overlapping content, unexplained internal shorthand and decorative animations that imply unobserved activity. Keep technical detail available through guides or progressive disclosure.

## Live tests and publication

Live calls are optional and require an authorized service/key supplied securely through the environment; follow [the live guide](docs/live-testing.md). Keep live results in ignored `results/` and mappings limited to the current invocation. Do not print credentials or mapping handles. Never include real customer/plant data, private specifications, internal audit narratives or team handoff files in commits, reports, screenshots, archives or release assets.

Respect the user's scope for remote changes and live operations. Inspect `git status` and the diff before staging. Keep dependency integrity enabled and review lockfile changes; released archives have stable bytes and checksums. Use a new version for changed release artifacts. Keep public documentation complete, accurate and linked to the user's next step. Report security issues through [SECURITY.md](SECURITY.md).
