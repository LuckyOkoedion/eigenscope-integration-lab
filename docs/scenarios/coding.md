# Coding worktree lab

Exercise the same practical boundary a coding assistant uses: an isolated Git worktree, a proposed source change, a real Python test command and verified cleanup.

```bash
uv sync --frozen --all-extras
uv run python -c 'from eigenscope_lab.simulators.coding import run_coding; import json; print(json.dumps(run_coding(), indent=2))'
```

Git 2.28 or newer must be on `PATH`. The scenario creates an entirely temporary repository and one synthetic commit. It never edits the integration-lab checkout or a user's project. Inherited Git redirection variables, global configuration and hooks are excluded, and synthetic author/committer details are supplied only to the child commands.

The baseline `retry_budget` function contains an off-by-one defect. Real `unittest` execution first fails. A separate worktree receives one permitted patch, passes the contract tests, and produces a diff affecting only the intended file. The original checkout remains unchanged. The worktree and temporary repository are removed.

The action interface is intentionally small:

```python
from pathlib import Path
from eigenscope_lab.simulators.coding import apply_approved_patch

apply_approved_patch(
    Path("temporary-agent-worktree"),
    "budget.py",
    "return attempts + 1",
    "return attempts",
)
```

Only the declared file is writable. Path escapes, symlink targets, stale matches and syntactically invalid patches reject before writing. Commands are fixed argument lists executed without a shell, with timeouts. This provides a bounded example action contract; arbitrary generated code should use the deployment's own OS/container isolation.

Pair this scenario with EigenScope by protecting synthetic organization, credential and repository context before the external provider step. Keep the requested refactor, test contract and allowed paths intact. Assert outbound privacy, restored identifiers and the resulting test outcome separately.

The fixture executes actual Git operations and Python subprocess tests. The proposed patch is deterministic, so results do not claim an IDE integration or model reasoning benchmark.

Sources: [Git worktree](https://git-scm.com/docs/git-worktree), [Git configuration](https://git-scm.com/docs/git-config), [Python unittest discovery](https://docs.python.org/3/library/unittest.html#test-discovery). Git remains a separately installed dependency under its upstream GPLv2 terms; no Git code is bundled.

## Production distribution clients

Roll out EigenScope across developer IDEs, coding agents, CI pipelines, and polyglot backend teams under `CODING_AGENT_FIREWALL`:

- **Product blueprint:** [EigenScope Coding Agent Firewall Guide](https://eigenscope.guardianai.network/guide/coding)
- **Antigravity IDE Native Firewall Plugin:** [`antigravity-plugin.zip`](https://eigenscope.guardianai.network/downloads/kits/antigravity-plugin.zip)
- **VS Code & Pi Coding Agent (`0.2.18`, `@eigenscope/pi-firewall@0.2.18`, Live RAM CoT Explorer):** [`vscode-pi-extension.zip`](https://eigenscope.guardianai.network/downloads/kits/vscode-pi-extension.zip) (also on VS Code Marketplace & Open VSX)
- **Meta Muse & Muse Code (`schema_version=1` `~/.config/muse/settings.json`, Hooks, Skill & CoT Explorer):** [`meta-muse-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/meta-muse-kit.zip)
- **Enterprise IDE Fleet & Jamf/Intune MDM Rollout Pack (`@eigenscope/init@0.2.18`):** [`ide-fleet-pack.zip`](https://eigenscope.guardianai.network/downloads/kits/ide-fleet-pack.zip) (Claude Code, Cursor, VS Code, Codex CLI, Meta Muse Code, GitHub Actions, Jamf Pro, Intune)
- **JetBrains (`0.2.18`, IntelliJ / PyCharm / GoLand CoT Explorer), ServiceNow & MCP Registry Kit:** [`jetbrains-servicenow-mcp-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/jetbrains-servicenow-mcp-kit.zip)
- **Enterprise Java 17 (Maven) & C# / .NET 8 (NuGet) SDKs:** [`enterprise-sdks-java-dotnet-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/enterprise-sdks-java-dotnet-kit.zip)
- **Full Distribution Hub:** [All 25 EigenScope Integrations & Kits](https://eigenscope.guardianai.network/integrations) · [Package Registry](https://eigenscope.guardianai.network/registry)
