# Legal & deal rooms: source terms and read-only review

Compare a synthetic Stock Purchase Agreement summary with a separately supplied source record. The local scenario extracts the indemnification cap, survival window and governing law, displays the observed values, and binds read-only review actions to a digest of those terms. Counterparty names, purchase price and escrow references belong in the local mapping rather than outbound model text.

## Run it

```sh
uv run --frozen eigenscope-lab run legal
uv run --frozen eigenscope-lab serve
```

Choose **Legal & deal rooms** in the dashboard to compare source and observed terms. The sample uses a 10% indemnification cap, an 18-month survival window and Delaware governing law. The Python API accepts other source terms:

```python
from decimal import Decimal
from eigenscope_lab.simulators.legal import DealTerms, run_legal

result = run_legal(
    source_terms=DealTerms(Decimal("12.5"), 24, "New York"),
    task_core="Summarize the 12.5% indemnification cap, 24-month survival window, and New York governing law.",
    sensitive_values=(),
)
assert all(check["passed"] for check in result["checks"])
```

## Checked contracts

| Boundary | Observation |
| --- | --- |
| Terms comparison | All three observed terms must equal the source record. Missing, conflicting or changed values fail; repeated identical terms remain one observation. Unrelated royalty and notice figures remain separate. |
| Outbound identifiers | Fixture identifiers are checked by an oracle outside the natural input. The local runner checks supplied text; live mode exercises automatic separation. |
| Read-only authorization | Only `summarize` and `request_review` actions with the matching terms digest are allowed. Payment, escrow, waiver and bank-change actions cannot authorize themselves through prose. |
| Rider controls | Active alteration/payment directives are rejected; negative safety instructions and quoted incident evidence have separate controls. |
| Result integrity | Metrics and the deterministic digest use the actual extracted values. Empty supplied text never falls back to a passing sample. |

The local engine performs structured extraction, source comparison and action authorization. The hosted workflow runs the official SDK's `LEGAL_CONTRACT_MA_ANALYTICS` policy, checks automatic identifier removal and commercial-term preservation, restores identifiers locally and verifies single-use mapping consumption:

```sh
uv run --frozen eigenscope-lab live --case legal --output results/legal-live.json
```

Configure the service and credentials using the [live guide](../live-testing.md). Start with the [EigenScope Legal guide](https://eigenscope.guardianai.network/guide/legal) for the product workflow. All local inputs are synthetic; no real deal-room documents are included.

## Loopback DMS Matter-Isolation & Clean-Room Contract

`run_legal()` also launches an isolated loopback HTTP DMS boundary (`run_dms_envelope_contract()`) modeling matter-scoped extraction for **iManage Work REST v2**, **NetDocuments**, **Microsoft 365**, and **Google Workspace**:

- Authorizes paragraph extraction for the target matter (`GET /api/v2/matters/MATTER-2026-01/documents/SPA-8.4`).
- Rejects cross-matter reads (`403 MATTER_SCOPE_VIOLATION`) and mutating DMS write-backs (`405 DMS_WRITEBACK_READ_ONLY`).
- Verifies that emitted counsel audit receipts preserve the SHA-256 checklist digest and `LEGAL_CONTRACT_MA_ANALYTICS` policy profile without raw counterparty, valuation, or escrow literals.

## Production distribution clients

Deploy EigenScope across law-firm DMS repositories, M&A virtual data rooms, and office suites without sending deal entities to external model providers:

- **Product blueprint:** [EigenScope Legal & M&A Deal Rooms Guide](https://eigenscope.guardianai.network/guide/legal)
- **iManage Work REST v2, NetDocuments & VDR Clean-Room Kit:** [`legal-imanage-netdocuments-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/legal-imanage-netdocuments-kit.zip)
- **Microsoft 365 Unified Privacy Shield (Word, Outlook, Excel):** [`m365-unified-addin.zip`](https://eigenscope.guardianai.network/downloads/kits/m365-unified-addin.zip) and [`word-addin-m365.zip`](https://eigenscope.guardianai.network/downloads/kits/word-addin-m365.zip)
- **Google Workspace Add-on (Docs, Gmail, Sheets):** [`google-workspace-addon.zip`](https://eigenscope.guardianai.network/downloads/kits/google-workspace-addon.zip)
- **Enterprise VDR Browser Extension (Chrome, Edge, Firefox & Safari):** [`browser-extension.zip`](https://eigenscope.guardianai.network/downloads/kits/browser-extension.zip)
- **SharePoint / S3 Legal Deal Room Batch Kit:** [`legal-dealroom-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/legal-dealroom-kit.zip)
- **Full Distribution Hub:** [All 24 EigenScope Integrations & Kits](https://eigenscope.guardianai.network/integrations) · [Package Registry](https://eigenscope.guardianai.network/registry)
