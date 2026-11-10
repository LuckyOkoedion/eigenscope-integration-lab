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
