"""The published SDK upgrade preserves strict default measurement validation."""

import json

import pytest

sdk = pytest.importorskip("eigenscope_sdk")
client_module = pytest.importorskip("eigenscope_sdk.client")


def baseline_response(request):
    digest = "sha256:" + "0" * 64
    return {
        "schema_version": 1,
        "request_id": request["request_id"],
        "status": "DECOMPOSED_OK",
        "task_core": "Compute 17 * 24.",
        "vault_ticket": "vlt_synthetic_lab_only",
        "private_symbol_summary": {
            "symbol_count": 0,
            "symbol_keys": [],
            "custody_mode": "LOCAL_MEMORY_VAULT_ONLY",
            "symbol_table_commitment": client_module.compute_symbol_table_commitment({}),
        },
        "core_proof_digest": digest,
        "style_vector": {
            "urgency_pressure": 0,
            "authority_framing": 0,
            "sycophancy_pull": 0,
            "verbosity_inflation": 0,
            "register_tag": "neutral_institutional_brief",
        },
        "noise_residual": {"dropped_spans": [], "filler_tokens_removed": 0},
        "telemetry": {
            "task_sufficiency_eta": 1,
            "mi_leakage_bound_bits": None,
            "token_compression_ratio_rho": 0,
            "raw_token_count": 4,
            "task_core_token_count": 4,
            "latency_ms": 1,
            "mi_measurement_scope": "accepted_v1_baseline",
            "bound_certified": False,
            "baseline_acceptance_sha256": digest,
            "baseline_package_digest": digest,
            "max_cross_subspace_cosine": 0,
        },
        "token_saliency": [],
    }


@pytest.mark.parametrize("explicit", [False, True])
def test_null_mi_requires_explicit_sdk_option(explicit):
    sent_headers = []

    def transport(path, headers, body):
        sent_headers.append(dict(headers))
        request = json.loads(body)
        if path == "/v1/vault/scrub":
            response = {
                "schema_version": 1,
                "request_id": request["request_id"],
                "status": "VAULT_TICKET_SCRUBBED",
                "vault_ticket": request["vault_ticket"],
                "vault_ticket_scrubbed": True,
            }
        else:
            response = baseline_response(request)
        return 200, json.dumps(response).encode()

    options = {"accept_v1_baseline": True} if explicit else {}
    with sdk.EigenScopeClient(
        bearer_token="synthetic-local-only", transport=transport, **options
    ) as client:
        if explicit:
            result = client.decompose("Compute 17 * 24.", return_token_saliency=False)
            assert result["telemetry"]["mi_leakage_bound_bits"] is None
            assert sent_headers[0]["X-OrthoCore-Accept-Baseline"] == "true"
        else:
            with pytest.raises(sdk.EigenScopeGateBlockedError) as error:
                client.decompose("Compute 17 * 24.", return_token_saliency=False)
            assert error.value.reason_code == "BASELINE_ACCEPTANCE_REQUIRED"
            assert all("X-OrthoCore-Accept-Baseline" not in row for row in sent_headers)
