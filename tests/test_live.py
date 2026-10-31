"""Lab orchestration controls using synthetic clients, never hosted service calls.

These fixture clients test the lab's attribution/cleanup and do not stand in for
successful execution of EigenScope's engine. No real credential or socket is used.
"""

from __future__ import annotations

import builtins
import json
import urllib.error
from types import SimpleNamespace
from typing import Any

import pytest

from eigenscope_lab import cli, live
from eigenscope_lab.fixtures import ARITHMETIC_CASES, CASES, ContractCase
from eigenscope_lab.reporting import outcome


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    monkeypatch.delenv("ORTHOCORE_API_KEY", raising=False)
    monkeypatch.setenv("ORTHOCORE_BASE_URL", "https://sdk.example.invalid")
    monkeypatch.setattr(live.importlib.metadata, "version", lambda _name: "fixture-version")
    from eigenscope_lab.simulators import provider

    class ForbiddenServer:
        def __init__(self, **_kwargs):
            raise AssertionError("Fixture-only tests must not open a socket")

    monkeypatch.setattr(provider, "ProviderServer", ForbiddenServer)


def sdk_error(status=410, code="VAULT_TICKET_SCRUBBED", reason="TICKET_ALREADY_CONSUMED"):
    sdk = pytest.importorskip("eigenscope_sdk")
    return sdk.EigenScopeError(
        "SYNTHETIC_EXCEPTION_MESSAGE_MUST_NOT_BE_EXPORTED",
        http_status=status,
        error_code=code,
        reason_code=reason,
    )


CASE = ContractCase("fixture", "coding", "A fixed synthetic input", ("Synthetic Co",), ("safe",))


class FixtureClient:
    def __init__(
        self, *, failure=None, reuse_error=None, core="safe [PRIVATE_001]", allow_reuse=False
    ):
        self.failure = failure
        self.reuse_error = reuse_error
        self.core = core
        self.owned_tickets = set()
        self.scrubbed = []
        self.methods = []
        self.restored = False
        self.allow_reuse = allow_reuse

    def _decompose(self, method):
        self.methods.append(method)
        if self.failure:
            raise self.failure
        self.owned_tickets.add("vlt_synthetic_owned_fixture")
        return {
            "vault_ticket": "vlt_synthetic_owned_fixture",
            "status": "DECOMPOSED_OK",
            "task_core": self.core,
            "private_symbol_summary": {"symbol_table_commitment": "fixture-commitment"},
        }

    def decompose(self, _prompt):
        return self._decompose("decompose")

    def sanitize_tool_output(self, _prompt):
        return self._decompose("sanitize_tool_output")

    def decompose_6g_ran_intent(self, _prompt):
        return self._decompose("decompose_6g_ran_intent")

    def sanitize_robotics_vla_observation(self, _prompt):
        return self._decompose("sanitize_robotics_vla_observation")

    def rehydrate(self, ticket, output, **_kwargs):
        assert ticket == "vlt_synthetic_owned_fixture"
        if self.restored and not self.allow_reuse:
            raise self.reuse_error or sdk_error()
        self.restored = True
        return {
            "round_trip_verification": {
                "exact_identifier_match": True,
                "vault_ticket_scrubbed": True,
            },
            "rehydrated_text": output.replace("[PRIVATE_001]", "Synthetic Co"),
        }

    def scrub(self, ticket):
        self.scrubbed.append(ticket)
        self.owned_tickets.discard(ticket)
        return {"vault_ticket_scrubbed": True}


def fixture_provider(server, failure=None):
    def create(**kwargs):
        if failure:
            raise failure
        messages = kwargs["messages"]
        server.requests.append({"messages": messages})
        message = SimpleNamespace(content=messages[-1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def execute(client, *, case=CASE, provider_failure=None):
    server = SimpleNamespace(requests=[])
    return live._sdk_case(
        case,
        client=client,
        provider=fixture_provider(server, provider_failure),
        provider_server=server,
        seed=7,
    )


def test_missing_live_key_is_configuration_error_before_execution(monkeypatch):
    monkeypatch.setattr(
        live, "_sdk_case", lambda *_a, **_k: pytest.fail("Execution must not begin")
    )
    with pytest.raises(ValueError, match="ORTHOCORE_API_KEY"):
        live.run_live()


@pytest.mark.parametrize("key", ["", " fixture-value", "fixture-value\nheader", "fixture-value "])
def test_invalid_key_configuration_is_not_echoed(monkeypatch, key):
    monkeypatch.setenv("ORTHOCORE_API_KEY", key)
    with pytest.raises(ValueError) as failure:
        live.run_live()
    assert "fixture-value" not in str(failure.value)


@pytest.mark.parametrize(
    "origin",
    [
        "http://sdk.example.invalid",
        "https://user:fixture-value@sdk.example.invalid",
        "https://sdk.example.invalid?token=fixture-value",
        "https://sdk.example.invalid\n",
    ],
)
def test_invalid_origin_prevents_live_execution(monkeypatch, origin):
    monkeypatch.setenv("ORTHOCORE_API_KEY", "fixture-value")
    monkeypatch.setenv("ORTHOCORE_BASE_URL", origin)
    with pytest.raises(ValueError):
        live.run_live()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"repeat": 0},
        {"repeat": 11},
        {"timeout": float("nan")},
        {"timeout": 0},
        {"case_id": "unknown"},
    ],
)
def test_invalid_run_controls_are_configuration_errors(monkeypatch, kwargs):
    monkeypatch.setenv("ORTHOCORE_API_KEY", "fixture-value")
    with pytest.raises(ValueError):
        live.run_live(**kwargs)


def test_missing_sdk_dependency_reports_setup_requirement(monkeypatch):
    original_import = builtins.__import__

    def isolated_import(name, *args, **kwargs):
        if name == "eigenscope_sdk":
            raise ModuleNotFoundError("Synthetic missing SDK", name="eigenscope_sdk")
        return original_import(name, *args, **kwargs)

    monkeypatch.setenv("ORTHOCORE_API_KEY", "fixture-value")
    monkeypatch.setattr(builtins, "__import__", isolated_import)
    with pytest.raises(RuntimeError, match="Install"):
        live.run_live()


def test_fixture_success_does_not_export_credentials_or_ticket():
    client = FixtureClient()
    row = execute(client)
    assert outcome(row) == "PASS", row
    encoded = json.dumps(row)
    assert "vlt_synthetic_owned_fixture" not in encoded
    assert "SYNTHETIC_EXCEPTION_MESSAGE" not in encoded
    assert client.scrubbed == []


def test_provider_harness_exception_is_error_and_owned_ticket_is_scrubbed():
    client = FixtureClient()
    row = execute(client, provider_failure=RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION_TEXT"))
    assert client.scrubbed == ["vlt_synthetic_owned_fixture"]
    assert outcome(row) == "ERROR", row
    assert "SYNTHETIC_PRIVATE_EXCEPTION_TEXT" not in json.dumps(row)


def test_structured_sdk_service_error_is_a_failed_contract():
    row = execute(
        FixtureClient(
            failure=sdk_error(503, "CORE_AUTHORITY_UNAVAILABLE", "PRIVATE_CORE_NOT_READY")
        )
    )
    assert outcome(row) == "FAIL"
    assert "PRIVATE_CORE_NOT_READY" in json.dumps(row)
    assert "SYNTHETIC_EXCEPTION_MESSAGE" not in json.dumps(row)


def test_sdk_wrapped_network_failure_is_blocked_not_service_rejection():
    sdk = pytest.importorskip("eigenscope_sdk")
    failure = sdk.EigenScopeAuthorityUnavailableError(
        "Synthetic transport unavailable", reason_code="PRIVATE_CORE_NOT_READY"
    )
    failure.__cause__ = urllib.error.URLError("SYNTHETIC_DNS_FAILURE_DETAIL")
    row = execute(FixtureClient(failure=failure))
    assert outcome(row) == "BLOCKED", row
    assert "SYNTHETIC_DNS_FAILURE_DETAIL" not in json.dumps(row)


def test_harness_error_with_code_attribute_is_still_harness_error():
    failure = RuntimeError("Synthetic local fixture exception")
    failure.error_code = "FIXTURE_INTERNAL_ERROR"
    row = execute(FixtureClient(failure=failure))
    assert outcome(row) == "ERROR", row


def test_owned_ticket_is_observed_before_sdk_decomposition_validation_and_scrubbed():
    sdk = pytest.importorskip("eigenscope_sdk")
    calls = []

    def transport(path, _headers, wire):
        body = json.loads(wire)
        calls.append((path, body))
        if path == "/v1/decompose":
            # Correct envelope identity, deliberately invalid decomposition
            # schema. The actual SDK guard must reject it before registration.
            response = {
                "schema_version": 1,
                "request_id": body["request_id"],
                "vault_ticket": "vlt_synthetic_prevalidation_fixture",
                "status": "DECOMPOSED_OK",
                "task_core": "safe",
            }
        else:
            assert path == "/v1/vault/scrub"
            response = {
                "schema_version": 1,
                "request_id": body["request_id"],
                "vault_ticket": body["vault_ticket"],
                "vault_ticket_scrubbed": True,
                "status": "VAULT_TICKET_SCRUBBED",
            }
        return 200, json.dumps(response).encode()

    client = live.make_observed_client(
        sdk.EigenScopeClient,
        base_url="https://sdk.example.invalid",
        bearer_token="synthetic-fixture-only",
        transport=transport,
    )
    client.owned_tickets.add("vlt_unrelated_prior_fixture")
    row = execute(client)
    assert outcome(row) == "FAIL", row
    assert [path for path, _ in calls] == ["/v1/decompose", "/v1/vault/scrub"]
    assert calls[-1][1]["vault_ticket"] == "vlt_synthetic_prevalidation_fixture"
    assert client.owned_tickets == {"vlt_unrelated_prior_fixture"}
    assert next(c for c in row["checks"] if c["name"] == "owned_ticket_cleanup")["passed"]
    assert "vlt_synthetic_prevalidation_fixture" not in json.dumps(row)
    assert not client._tickets  # The rejected SDK response never registered it.


def test_unrelated_bad_request_does_not_prove_consumed_ticket_denial():
    client = FixtureClient(
        reuse_error=sdk_error(400, "INVALID_REQUEST_SCHEMA", "UNKNOWN_OR_MISSING_FIELD")
    )
    row = execute(client)
    denial = next(c for c in row["checks"] if c["name"] == "consumed_ticket_reuse_denied")
    assert denial["passed"] is False
    assert outcome(row) == "FAIL"
    assert client.scrubbed == ["vlt_synthetic_owned_fixture"]


def test_claimed_consumed_ticket_that_reuses_successfully_is_still_scrubbed():
    client = FixtureClient(allow_reuse=True)
    row = execute(client)
    denial = next(c for c in row["checks"] if c["name"] == "consumed_ticket_reuse_denied")
    assert denial["passed"] is False
    assert outcome(row) == "FAIL"
    assert client.scrubbed == ["vlt_synthetic_owned_fixture"]


def test_tool_case_exercises_the_advertised_tool_policy():
    client = FixtureClient(core="Build target=linux-x64 with lto=thin.")
    row = execute(client, case=next(case for case in CASES if case.id == "tool-injection"))
    assert outcome(row) == "PASS", row
    assert client.methods == ["sanitize_tool_output"]


def test_missing_industry_dependency_is_blocked_not_product_failure(monkeypatch):
    from eigenscope_lab.simulators import robotics

    def missing_engine(**_kwargs):
        raise ModuleNotFoundError("Synthetic missing optional engine", name="mujoco")

    monkeypatch.setattr(robotics, "run_robotics", missing_engine)
    case = ContractCase("fixture-robot", "robotics", "Fixed input", (), ("safe",))
    row = execute(FixtureClient(), case=case)
    assert outcome(row) == "BLOCKED", row


def test_wrapped_missing_industry_dependency_is_also_blocked(monkeypatch):
    from eigenscope_lab.simulators import robotics

    def missing_engine(**_kwargs):
        try:
            raise ModuleNotFoundError("Synthetic missing optional engine", name="mujoco")
        except ModuleNotFoundError as missing:
            raise RuntimeError("Install the robotics extra") from missing

    monkeypatch.setattr(robotics, "run_robotics", missing_engine)
    case = ContractCase("fixture-robot", "robotics", "Fixed input", (), ("safe",))
    row = execute(FixtureClient(), case=case)
    assert outcome(row) == "BLOCKED", row


def test_optional_engine_block_does_not_hide_confirmed_privacy_failure(monkeypatch):
    from eigenscope_lab.simulators import robotics

    def missing_engine(**_kwargs):
        raise ModuleNotFoundError("Synthetic missing optional engine", name="mujoco")

    monkeypatch.setattr(robotics, "run_robotics", missing_engine)
    case = ContractCase("fixture-robot", "robotics", "Fixed input", ("Synthetic Co",), ("safe",))
    row = execute(FixtureClient(core="safe Synthetic Co"), case=case)
    assert (
        next(c for c in row["checks"] if c["name"] == "private_values_absent_from_outbound_core")[
            "passed"
        ]
        is False
    )
    assert outcome(row) == "FAIL", row


class FixtureResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self.payload = payload

    def json(self):
        return self.payload


def workbench_transport(
    monkeypatch, *, consumed=True, invalid_success=False, invalid_status=400, malformed=False
):
    httpx = pytest.importorskip("httpx")
    calls: list[dict[str, Any]] = []
    answers = {prompt: answer for _, prompt, answer in ARITHMETIC_CASES}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, *, json):
            calls.append({"url": url, "body": json})
            if url.endswith("/vault-zeroize"):
                return FixtureResponse(
                    200, {"status": "VAULT_TICKET_SCRUBBED", "vault_ticket_scrubbed": True}
                )
            if not isinstance(json["policy"], dict) and not invalid_success:
                return FixtureResponse(
                    invalid_status,
                    {
                        "error_code": "INVALID_POLICY_PARAMETERS"
                        if invalid_status == 400
                        else "AUTHENTICATION_REQUIRED"
                    },
                )
            if malformed:
                return FixtureResponse(
                    200,
                    {
                        "session": {
                            "ux2Diff": {
                                "pane2": {"vaultTicket": "vlt_synthetic_workbench_fixture"},
                                "pane3": {},
                            }
                        }
                    },
                )
            return FixtureResponse(
                200,
                {
                    "session": {
                        "ux2Diff": {
                            "pane2": {"vaultTicket": "vlt_synthetic_workbench_fixture"},
                            "pane3": {
                                "rehydratedOutputText": answers.get(json["rawPrompt"], "408"),
                                "vaultTicketScrubbed": consumed,
                            },
                        }
                    }
                },
            )

    monkeypatch.setattr(httpx, "Client", Client)
    return calls


def test_workbench_uses_actual_published_path_and_independent_answers(monkeypatch):
    calls = workbench_transport(monkeypatch)
    rows = live._workbench_cases("https://sdk.example.invalid", 3)
    assert len(rows) == len(ARITHMETIC_CASES) + 3
    assert all(outcome(row) == "PASS" for row in rows)
    assert all(
        call["url"] == "https://sdk.example.invalid/api/workbench/decompose" for call in calls
    )


def test_workbench_unconsumed_owned_tickets_are_explicitly_zeroized(monkeypatch):
    calls = workbench_transport(monkeypatch, consumed=False)
    rows = live._workbench_cases("https://sdk.example.invalid", 3)
    zeroize = [call for call in calls if call["url"].endswith("/vault-zeroize")]
    assert len(zeroize) == len(ARITHMETIC_CASES)
    assert all(
        call["body"] == {"vault_ticket": "vlt_synthetic_workbench_fixture"} for call in zeroize
    )
    assert all(outcome(row) == "FAIL" for row in rows[: len(ARITHMETIC_CASES)])
    assert "vlt_synthetic_workbench_fixture" not in json.dumps(rows)


def test_unexpected_invalid_policy_success_is_failed_and_still_cleaned(monkeypatch):
    calls = workbench_transport(monkeypatch, consumed=False, invalid_success=True)
    rows = live._workbench_cases("https://sdk.example.invalid", 3)
    invalid_rows = rows[len(ARITHMETIC_CASES) :]
    assert all(outcome(row) == "FAIL" for row in invalid_rows)
    assert len([call for call in calls if call["url"].endswith("/vault-zeroize")]) == len(rows)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_or_rate_denial_does_not_prove_invalid_policy_validation(monkeypatch, status):
    workbench_transport(monkeypatch, invalid_status=status)
    rows = live._workbench_cases("https://sdk.example.invalid", 3)
    for row in rows[len(ARITHMETIC_CASES) :]:
        check = next(
            check for check in row["checks"] if check["name"] == "malformed_policy_rejected"
        )
        assert check["passed"] is False


def test_malformed_success_response_is_contract_failure_and_owned_ticket_is_cleaned(monkeypatch):
    calls = workbench_transport(monkeypatch, malformed=True)
    rows = live._workbench_cases("https://sdk.example.invalid", 3)
    assert all(outcome(row) == "FAIL" for row in rows[: len(ARITHMETIC_CASES)]), rows
    assert len([call for call in calls if call["url"].endswith("/vault-zeroize")]) == len(
        ARITHMETIC_CASES
    )


def test_workbench_cleanup_does_not_touch_unrelated_or_missing_ticket():
    class ForbiddenHTTP:
        def post(self, *_args, **_kwargs):
            pytest.fail("No owned handle was returned; cleanup must not send a request")

    assert live.cleanup_workbench(ForbiddenHTTP(), "https://sdk.example.invalid", None) is None
    assert (
        live.cleanup_workbench(
            ForbiddenHTTP(),
            "https://sdk.example.invalid",
            {
                "session": {
                    "ux2Diff": {"pane2": {"vaultTicket": "not-a-vault-ticket"}, "pane3": {}}
                },
            },
        )
        is None
    )


@pytest.mark.parametrize("status", [400, 422])
def test_policy_gate_rejection_is_not_schema_validation(monkeypatch, status):
    import httpx

    workbench_transport(monkeypatch)
    original = httpx.Client

    class GateClient(original):
        def post(self, url, *, json):
            if not isinstance(json.get("policy"), dict):
                return FixtureResponse(status, {"error_code": "SEPARATION_GATE_BLOCKED"})
            return super().post(url, json=json)

    monkeypatch.setattr(httpx, "Client", GateClient)
    rows = live._workbench_cases("https://sdk.example.invalid", 3)
    assert all(outcome(row) == "FAIL" for row in rows[-3:])


def test_cli_does_not_echo_an_unexpected_runtime_exception(monkeypatch, capsys):
    def broken_runner(**_kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_RUNTIME_DETAIL")

    monkeypatch.setattr(live, "run_live", broken_runner)
    try:
        exit_code = cli.main(["live"])
    except SystemExit as exited:
        exit_code = exited.code
    assert exit_code != 0
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_RUNTIME_DETAIL" not in output.err + output.out
