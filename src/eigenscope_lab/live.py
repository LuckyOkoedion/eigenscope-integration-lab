"""Real official SDK/API checks. Only the provider is simulated.

No authentication values, raw exception messages, response bodies, session links
or vault-ticket values are included in public results. Every input is a fixed
synthetic case in fixtures.py. No customer-supplied prompt option exists.
"""

from __future__ import annotations

import importlib.metadata
import math
import os
import re
import time
import urllib.error
import urllib.parse
from contextlib import closing
from typing import Any

from .fixtures import ARITHMETIC_CASES, CASES, ContractCase
from .reporting import percentiles, report


class ServiceResponseContractError(Exception):
    """An actual HTTP success body violated the service response contract."""


def validate_origin(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("ORTHOCORE_BASE_URL must not contain whitespace or control characters")
    try:
        parsed = urllib.parse.urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("ORTHOCORE_BASE_URL must be a valid HTTPS origin") from exc
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            )
        )
    ):
        raise ValueError(
            "Use an HTTPS origin or loopback HTTP origin without a path, credentials, query or fragment"
        )
    return value.rstrip("/")


def safe_error(exc: Exception) -> dict[str, str | int]:
    result: dict[str, str | int] = {"exception": type(exc).__name__}
    for name in ("error_code", "reason_code"):
        value = getattr(exc, name, None)
        if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value):
            result[name] = value
    status = getattr(exc, "http_status", None)
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    return result


def _check(name: str, passed: bool, **extra: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), **extra}


def make_observed_client(client_type: type, **kwargs: Any) -> Any:
    """Observe owned tickets before all SDK guards using its public transport hook."""
    from .transports import ObservedTransport

    class OwnedTicketClient(client_type):
        def __init__(self, **options: Any):
            self.owned_tickets: set[str] = set()
            self._observed_transport = ObservedTransport(
                base_url=options.get("base_url", "http://127.0.0.1:8443"),
                timeout_seconds=options.get("timeout_seconds", 10.0),
                owned_tickets=self.owned_tickets,
                delegate=options.pop("transport", None),
            )
            try:
                super().__init__(transport=self._observed_transport, **options)
            except Exception:
                self._observed_transport.close()
                raise

        def close(self) -> None:
            self._observed_transport.close()

    return OwnedTicketClient(**kwargs)


def _record_exception(row: dict[str, Any], exc: Exception) -> None:
    detail = safe_error(exc)
    causes: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(causes) < 8 and current not in causes:
        causes.append(current)
        current = current.__cause__ or current.__context__
    try:
        import httpx

        request_errors: tuple[type[BaseException], ...] = (httpx.RequestError,)
    except ImportError:
        request_errors = ()
    if any(isinstance(cause, (ImportError, ModuleNotFoundError)) for cause in causes):
        row["status"] = "BLOCKED"
    elif any(
        isinstance(cause, (OSError, TimeoutError, urllib.error.URLError))
        and not isinstance(cause, urllib.error.HTTPError)
        for cause in causes
    ):
        row["status"] = "BLOCKED"
    elif any(isinstance(cause, request_errors) for cause in causes):
        row["status"] = "BLOCKED"
    elif (
        detail.get("reason_code") in {"SIDECAR_TRANSPORT_UNREACHABLE"}
        or detail.get("http_status") == 401
    ):
        row["status"] = "BLOCKED"
    elif type(exc).__module__.startswith("eigenscope_sdk") or isinstance(
        exc, ServiceResponseContractError
    ):
        # A genuine SDK/API contract rejection is a failed published workflow.
        pass
    else:
        row["status"] = "ERROR"
    if row.get("status") == "BLOCKED" and any(
        check.get("passed") is False for check in row["checks"]
    ):
        row["interruption_status"] = "BLOCKED"
        row.pop("status")
    row["checks"].append(_check("workflow_completed", False, **detail))


def evaluate_core(case: ContractCase, core: str) -> list[dict[str, Any]]:
    folded = core.casefold()
    return [
        _check(
            "private_values_absent_from_outbound_core",
            all(value.casefold() not in folded for value in case.private_values),
            matched_private_value_count=sum(
                value.casefold() in folded for value in case.private_values
            ),
        ),
        _check(
            "task_terms_preserved", all(value.casefold() in folded for value in case.required_terms)
        ),
        _check(
            "untrusted_directives_absent",
            all(value.casefold() not in folded for value in case.forbidden_terms),
        ),
    ]


def _sdk_case(
    case: ContractCase, *, client: Any, provider: Any, provider_server: Any, seed: int
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "suite": f"sdk:{case.id}",
        "engine": "official EigenScope Python SDK + real loopback OpenAI HTTP client",
        "sdk_version": importlib.metadata.version("eigenscope-sdk"),
        "checks": [],
        "metrics": {},
        "scope": "Actual hosted decomposition/rehydration; deterministic external provider; published synthetic input only.",
    }
    checks = row["checks"]
    ticket: str | None = None
    consumed = False
    owned_before = set(getattr(client, "owned_tickets", set()))
    started = time.perf_counter()
    try:
        before = time.perf_counter()
        if case.domain == "telecom":
            dec = client.decompose_6g_ran_intent(case.prompt)
        elif case.domain == "robotics":
            dec = client.sanitize_robotics_vla_observation(case.prompt)
        elif case.domain == "tools":
            dec = client.sanitize_tool_output(case.prompt)
        else:
            dec = client.decompose(case.prompt)
        row["metrics"]["decompose_sdk_wall_ms"] = round((time.perf_counter() - before) * 1000, 3)
        ticket = dec["vault_ticket"]
        checks.append(_check("actual_decomposition_succeeded", dec["status"] == "DECOMPOSED_OK"))
        core = dec["task_core"]
        checks.extend(evaluate_core(case, core))
        before = time.perf_counter()
        completion = provider.chat.completions.create(
            model="lab-echo", messages=[{"role": "user", "content": core}], timeout=5
        )
        output = completion.choices[0].message.content
        row["metrics"]["local_provider_http_wall_ms"] = round(
            (time.perf_counter() - before) * 1000, 3
        )
        checks.append(
            _check(
                "provider_received_only_protected_core",
                provider_server.requests[-1]["messages"] == [{"role": "user", "content": core}],
            )
        )
        checks.append(_check("provider_preserved_symbols_exactly", output == core))
        before = time.perf_counter()
        restored = client.rehydrate(ticket, output)
        row["metrics"]["rehydrate_sdk_wall_ms"] = round((time.perf_counter() - before) * 1000, 3)
        verification = restored["round_trip_verification"]
        consumed = verification["vault_ticket_scrubbed"] is True
        checks.append(
            _check(
                "identifiers_restored_and_ticket_consumed",
                verification["exact_identifier_match"] is True and consumed,
            )
        )
        checks.append(
            _check(
                "masked_identifiers_returned_locally",
                all(
                    value in restored["rehydrated_text"]
                    for value in case.private_values
                    if value not in core
                ),
            )
        )
        try:
            client.rehydrate(
                ticket,
                output,
                expected_symbol_table_commitment=dec["private_symbol_summary"][
                    "symbol_table_commitment"
                ],
            )
        except Exception as exc:
            denial = safe_error(exc)
            proven_denial = denial.get("http_status") == 410 and (
                denial.get("error_code") == "VAULT_TICKET_SCRUBBED"
                or denial.get("reason_code")
                in {"TICKET_ALREADY_CONSUMED", "TICKET_ZEROIZED_OR_TTL_EXPIRED"}
            )
            checks.append(_check("consumed_ticket_reuse_denied", proven_denial, **denial))
            consumed = consumed and proven_denial
        else:
            checks.append(_check("consumed_ticket_reuse_denied", False))
            consumed = False
        if case.domain in {"robotics", "telecom"}:
            if case.domain == "robotics":
                from .simulators.robotics import run_robotics

                external = run_robotics(seed=seed, task_core=core)
            else:
                from .simulators.telecom import run_telecom

                external = run_telecom(seed=seed, task_core=core)
            row["external_system"] = external
            checks.append(
                _check(
                    "industry_adapter_checks_pass",
                    all(c["passed"] is True for c in external["checks"]),
                )
            )
    except Exception as exc:
        _record_exception(row, exc)
    finally:
        owned_now = set(getattr(client, "owned_tickets", set())) - owned_before
        if ticket:
            owned_now.add(ticket)
        if consumed and ticket:
            owned_now.discard(ticket)
            getattr(client, "owned_tickets", set()).discard(ticket)
        for owned_ticket in sorted(owned_now):
            try:
                client.scrub(owned_ticket)
                getattr(client, "owned_tickets", set()).discard(owned_ticket)
                checks.append(_check("owned_ticket_cleanup", True))
            except Exception as exc:
                detail = safe_error(exc)
                proven_gone = detail.get("http_status") == 410 and (
                    detail.get("error_code") == "VAULT_TICKET_SCRUBBED"
                    or detail.get("reason_code")
                    in {"TICKET_ALREADY_CONSUMED", "TICKET_ZEROIZED_OR_TTL_EXPIRED"}
                )
                if proven_gone:
                    getattr(client, "owned_tickets", set()).discard(owned_ticket)
                checks.append(_check("owned_ticket_cleanup", proven_gone, **detail))
        row["metrics"]["complete_scenario_wall_ms"] = round(
            (time.perf_counter() - started) * 1000, 3
        )
    return row


def cleanup_workbench(http: Any, base_url: str, data: Any) -> dict[str, Any] | None:
    """Clean only a ticket returned by this invocation's own workbench request."""
    try:
        diff = data["session"]["ux2Diff"]
        consumed = diff.get("pane3", {}).get("vaultTicketScrubbed") is True
        ticket = diff.get("pane2", {}).get("vaultTicket")
    except (KeyError, TypeError, AttributeError):
        return None
    if consumed or not isinstance(ticket, str) or not ticket.startswith("vlt_"):
        return None
    try:
        response = http.post(
            f"{base_url}/api/workbench/vault-zeroize", json={"vault_ticket": ticket}
        )
        result = response.json()
        return _check(
            "owned_workbench_ticket_cleanup",
            response.status_code == 200 and result.get("vault_ticket_scrubbed") is True,
        )
    except Exception as exc:
        return _check("owned_workbench_ticket_cleanup", False, **safe_error(exc))


def _workbench_cases(base_url: str, timeout: float) -> list[dict[str, Any]]:
    """Exercise the public workbench's own answer path, not a model stub answer."""
    import httpx

    rows = []

    def read_response(response: Any) -> Any:
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise ServiceResponseContractError("Invalid workbench JSON") from exc

    def parse_result(data: Any) -> Any:
        try:
            result = data["session"]["ux2Diff"]["pane3"]
            if (
                not isinstance(result["rehydratedOutputText"], str)
                or type(result["vaultTicketScrubbed"]) is not bool
            ):
                raise ValueError("invalid result types")
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceResponseContractError("Invalid workbench response") from exc

    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
        for name, prompt, expected in ARITHMETIC_CASES:
            row: dict[str, Any] = {
                "suite": f"workbench:{name}",
                "engine": "actual public workbench API",
                "checks": [],
                "scope": "Known synthetic arithmetic input; actual workbench answer independently compared to exact expected integer.",
            }
            started = time.perf_counter()
            data = None
            try:
                response = http.post(
                    f"{base_url}/api/workbench/decompose",
                    json={
                        "rawPrompt": prompt,
                        "model_id": "gemma-2-2b",
                        "policy": {
                            "target_task_equivalencefloor": 0.995,
                            "max_mi_leakage_bits": 0.01,
                        },
                    },
                )
                row["http_status"] = response.status_code
                row["checks"].append(_check("transport_success", response.status_code == 200))
                if response.status_code == 200:
                    data = read_response(response)
                    result = parse_result(data)
                    actual = str(result["rehydratedOutputText"]).strip()
                    row["checks"].append(
                        _check(
                            "exact_answer",
                            actual == expected,
                            expected=expected,
                            actual=actual[:100]
                            if re.fullmatch(r"[-+\d.\s]{1,100}", actual)
                            else "non-numeric-output",
                        )
                    )
                    row["checks"].append(
                        _check("ticket_consumed", result.get("vaultTicketScrubbed") is True)
                    )
            except Exception as exc:
                _record_exception(row, exc)
            finally:
                cleaned = cleanup_workbench(http, base_url, data)
                if cleaned:
                    row["checks"].append(cleaned)
            row["metrics"] = {
                "http_full_body_wall_ms": round((time.perf_counter() - started) * 1000, 3)
            }
            rows.append(row)
        for name, invalid in (("array", []), ("string", "invalid-policy"), ("number", 7)):
            row = {
                "suite": f"workbench:invalid-policy-{name}",
                "engine": "actual public API schema validation",
                "checks": [],
            }
            data = None
            try:
                response = http.post(
                    f"{base_url}/api/workbench/decompose",
                    json={
                        "rawPrompt": "Calculate 17 * 24.",
                        "model_id": "gemma-2-2b",
                        "policy": invalid,
                    },
                )
                row["http_status"] = response.status_code
                schema_denied = False
                if response.status_code in {400, 422}:
                    try:
                        rejection = response.json()
                        code = rejection.get("error_code", rejection.get("error"))
                        schema_denied = code in {
                            "INVALID_POLICY_PARAMETERS",
                            "INVALID_REQUEST_SCHEMA",
                        }
                    except (ValueError, TypeError, AttributeError):
                        pass
                row["checks"].append(_check("malformed_policy_rejected", schema_denied))
                if response.status_code == 200:
                    data = read_response(response)
                    result = parse_result(data)
                    row["checks"].append(
                        _check(
                            "unexpected_success_ticket_consumed",
                            result.get("vaultTicketScrubbed") is True,
                        )
                    )
            except Exception as exc:
                _record_exception(row, exc)
            finally:
                cleaned = cleanup_workbench(http, base_url, data)
                if cleaned:
                    row["checks"].append(cleaned)
            rows.append(row)
    return rows


def run_live(
    *, seed: int = 7, repeat: int = 1, case_id: str = "all", timeout: float = 15.0
) -> dict[str, Any]:
    if type(seed) is not int or not 0 <= seed <= 4294967295:
        raise ValueError("seed must be an integer between 0 and 4294967295")
    if type(repeat) is not int or not 1 <= repeat <= 10:
        raise ValueError("repeat must be an integer between 1 and 10")
    if not math.isfinite(timeout) or not 1 <= timeout <= 60:
        raise ValueError("timeout must be between 1 and 60 seconds")
    base_url = validate_origin(
        os.environ.get("ORTHOCORE_BASE_URL", "https://eigenscope.guardianai.network")
    )
    key = os.environ.get("ORTHOCORE_API_KEY", "")
    if not key or key != key.strip() or any(c in key for c in "\r\n"):
        raise ValueError(
            "Configure ORTHOCORE_API_KEY with an authorized API key; its value is never printed"
        )
    if case_id not in {"all", "workbench", *(case.id for case in CASES)}:
        raise ValueError(
            "Unknown case; choose all, workbench, coding, tool-injection, telecom, robotics, email or unicode"
        )
    try:
        import httpx
        from eigenscope_sdk import EigenScopeClient
        from openai import OpenAI

        from .simulators.provider import ProviderServer
    except ImportError as exc:
        raise RuntimeError("Install live and provider extras: uv sync --all-extras") from exc
    suites: list[dict[str, Any]] = []
    with (
        ProviderServer() as server,
        OpenAI(
            base_url=server.base_url,
            api_key="local-simulator-only",
            max_retries=0,
            http_client=httpx.Client(trust_env=False),
        ) as provider,
    ):
        with closing(
            make_observed_client(
                EigenScopeClient, base_url=base_url, bearer_token=key, timeout_seconds=timeout
            )
        ) as client:
            for iteration in range(repeat):
                for case in CASES:
                    if case_id not in {"all", case.id}:
                        continue
                    result = _sdk_case(
                        case, client=client, provider=provider, provider_server=server, seed=seed
                    )
                    result["iteration"] = iteration + 1
                    suites.append(result)
            if case_id in {"all", "workbench"}:
                suites.extend(_workbench_cases(base_url, timeout))
    result = report(suites, "actual-sdk-and-api", seed)
    samples = [
        suite["metrics"]["decompose_sdk_wall_ms"]
        for suite in suites
        if "decompose_sdk_wall_ms" in suite.get("metrics", {})
    ]
    result["measurement_boundaries"] = {
        "decompose_sdk_wall_ms": "Official SDK serialization, HTTP network, server execution and response validation; no provider time.",
        "local_provider_http_wall_ms": "Real loopback HTTP request to deterministic external-provider simulator.",
        "complete_scenario_wall_ms": "Whole scenario, including provider, rehydration, reuse negative control and optional industry engine.",
        "warmups": 0,
        "concurrency": 1,
        "repeat_applies_to": "SDK cases only; public workbench cases run once.",
    }
    if samples:
        result["sdk_decompose_observations"] = percentiles(samples)
    return result
