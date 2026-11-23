# Finance & data: SQL UDF batching, Decimal ledger & dual-control gate

Run a synthetic double-entry treasury ledger (`decimal.Decimal` exact cents), register a real SQL scalar UDF (`eigenscope_protect_row`) inside an in-memory SQLite engine that calls a loopback External Access HTTP gate, enforce single-use dual-control compliance approvals, and verify content-free audit receipts.

All ledger rows, IBAN/SWIFT values, account numbers, and counterparties are synthetic fixtures; no bank settlement rail or live Snowflake/Databricks cluster is contacted.

## Run it

```sh
uv sync --locked --all-extras
uv run --frozen eigenscope-lab run finance
uv run --frozen pytest tests/test_finance.py
```

## What runs

| Stage | Engine / Contract | Expected verification |
| --- | --- | --- |
| `ledger-ingest` | Natural parameter & entity boundary | Retains `EUR`, `T+1`, `15 bps`, and `dual-control`; verifies company, IBAN, SWIFT, amount, and account entities are absent from outbound text. |
| `sql-udf-batch` | In-memory SQLite SQL + External Access HTTP | Verifies `SUM(debits) == SUM(credits)` (`485,000,000` cents), executes `eigenscope_protect_row(...)` across partitions, and checks zero sensitive values in SQL UDF outputs or audit receipts. |
| `treasury-authorization` | `LocalDualControlApprovals` + HTTP gate | Accepts a valid single-use `X-Dual-Control-Approval` token once, rejects unapproved or replayed reviews (`422`), and blocks wire disbursement endpoints (`/udf/execute-wire`, `operation="execute_wire"`). |
| `adversarial-wire-injection` | Prompt-injection boundary | Rejects counterparty remittance notes attempting to bypass compliance holds or redirect settlement funds to external endpoints while admitting benign negative policy statements. |

## Exercise the real EigenScope boundary

```sh
export ORTHOCORE_BASE_URL=https://eigenscope.guardianai.network
# Set ORTHOCORE_API_KEY securely in your shell environment.
uv run --frozen eigenscope-lab live --case finance --output results/finance-live.json
```

The TypeScript suite also includes a `finance` case in `typescript/examples/live.ts` (`cd typescript && npm run live`).

## Production distribution clients

When moving from the local integration lab into production cloud data warehouses, banking services, and treasury portals, use the matching EigenScope production clients (strict `decompose -> customer provider call -> rehydrate -> scrub`, zero middleware):

- **Product blueprint:** [EigenScope Financial Services & Banking Guide](https://eigenscope.guardianai.network/guide/finance)
- **Snowflake External Access & Databricks Unity Catalog Kit:** [`finance-banking-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/finance-banking-kit.zip) (Snowflake Native App manifest + SQL/Python UDFs, Databricks `mapInPandas` PySpark transform, Treasury wire/SWIFT/IBAN/KYC clean-room worker)
- **Enterprise Java 17 (Maven) & C# / .NET 8 (NuGet) SDKs:** [`enterprise-sdks-java-dotnet-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/enterprise-sdks-java-dotnet-kit.zip) (`io.eigenscope:eigenscope-sdk:0.2.17` & `EigenScope.Sdk` v0.2.17)
- **Office & Spreadsheet Staff Flows:** [`m365-unified-addin.zip`](https://eigenscope.guardianai.network/downloads/kits/m365-unified-addin.zip) (Word, Excel & Outlook) and [`google-workspace-addon.zip`](https://eigenscope.guardianai.network/downloads/kits/google-workspace-addon.zip) (Docs, Sheets & Gmail)
- **Full Distribution Hub:** [All 24 EigenScope Integrations & Kits](https://eigenscope.guardianai.network/integrations) · [Package Registry](https://eigenscope.guardianai.network/registry)
