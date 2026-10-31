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
