# Security

## Report a vulnerability

Use this repository's GitHub **Security → Report a vulnerability** when private vulnerability reporting is enabled. If unavailable, contact the maintainers through the contact route on [EigenScope](https://eigenscope.guardianai.network/) and request a private channel. Do not put credentials or sensitive reproductions in public issues.

## Execution model

- The dashboard binds loopback by default. It runs published synthetic scenarios only and never reads API credentials. Keep it on a trusted local workstation.
- Live SDK tests are explicit CLI operations using your own authorized key. Generated mappings belong to that invocation and are consumed or scrubbed. Test inputs are fixed synthetic fixtures.
- The OpenAI-compatible server is a local deterministic fixture. It never forwards prompts externally and does not retain authorization headers.
- MCP tools and coding actions are narrowly scoped to synthetic content and temporary repositories. No arbitrary shell command or user-selected filesystem path is exposed by the dashboard.
- Optional telecom containers require the documented Linux capabilities for virtual networking. Run only the isolated lab stack. Its SIM credentials are public test fixtures, unsuitable for a deployed network.
- Ordinary CI does not receive production secrets or execute live service tests. Review runtime reports before public sharing.

The initial supported release is 0.1.x. Security fixes are applied to the latest release. Third-party tools remain subject to their own security and support policies.
