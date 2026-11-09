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
from collections.abc import Callable
from contextlib import closing
from typing import Any

from .fixtures import ARITHMETIC_CASES, CASES, ContractCase
from .reporting import outcome, percentiles, report


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


def _checkpoint(check_cancelled: Callable[[], None] | None) -> None:
    if check_cancelled is not None:
        check_cancelled()


def make_observed_client(client_type: type, **kwargs: Any) -> Any:
    """Observe owned tickets before all SDK guards using its public transport hook."""
    from .transports import ObservedTransport

    class OwnedTicketClient(client_type):
        owned_tickets: Any = None

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


def describe_suite_guidance(checks: list[dict[str, Any]]) -> str | None:
    if not checks or all(check.get("passed") is True for check in checks):
        return None
    failed = [check for check in checks if check.get("passed") is not True]
    if any(check.get("http_status") == 503 for check in failed):
        return (
            "The configured service returned HTTP 503. Retry the run or check the service status."
        )
    if any(check.get("http_status") in {401, 403} for check in failed):
        return "Request was not authorized (HTTP 401/403). Verify ORTHOCORE_API_KEY permissions."
    if any(check.get("http_status") == 429 for check in failed):
        return "Request was rate-limited (HTTP 429). Retry shortly."
    privacy_ok = any(
        check.get("name") == "private_values_absent_from_outbound_core"
        and check.get("passed") is True
        for check in checks
    )
    directive_flagged = any(
        (
            check.get("name") == "untrusted_directives_absent"
            or str(check.get("name", "")).startswith("provided_task_core_respects_")
        )
        and check.get("passed") is not True
        for check in failed
    )
    if privacy_ok and directive_flagged:
        return "Identifier separation passed; embedded instruction filtering did not pass under the active policy."
    if any(
        str(check.get("name", "")).startswith("rag_") and check.get("passed") is not True
        for check in failed
    ):
        return "RAG checks did not pass. Review the named checks and the run report."
    return "Review the flagged checks against your service configuration."


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
    case: ContractCase,
    *,
    client: Any,
    provider: Any,
    provider_server: Any,
    seed: int,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
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
        _checkpoint(check_cancelled)
        before = time.perf_counter()
        if case.domain == "telecom":
            dec = client.decompose_6g_ran_intent(case.prompt)
        elif case.domain == "robotics":
            dec = client.sanitize_robotics_vla_observation(case.prompt)
        elif case.domain == "oil-gas" and hasattr(client, "sanitize_oil_gas_production_context"):
            dec = client.sanitize_oil_gas_production_context(case.prompt)
        elif case.domain == "healthcare" and hasattr(client, "sanitize_clinical_phi"):
            dec = client.sanitize_clinical_phi(case.prompt)
        elif case.domain in {"tools", "rag"}:
            dec = client.sanitize_tool_output(case.prompt)
        else:
            dec = client.decompose(case.prompt)
        row["metrics"]["decompose_sdk_wall_ms"] = round((time.perf_counter() - before) * 1000, 3)
        ticket = dec["vault_ticket"]
        checks.append(_check("actual_decomposition_succeeded", dec["status"] == "DECOMPOSED_OK"))
        core = dec["task_core"]
        checks.extend(evaluate_core(case, core))
        _checkpoint(check_cancelled)
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
        _checkpoint(check_cancelled)
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
        _checkpoint(check_cancelled)
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
        _checkpoint(check_cancelled)
        if case.domain in {"robotics", "telecom", "oil-gas", "healthcare", "rag"}:
            if case.domain == "robotics":
                from .simulators.robotics import run_robotics

                external = run_robotics(seed=seed, task_core=core, on_frame=on_frame)
            elif case.domain == "telecom":
                from .simulators.telecom import run_telecom

                external = run_telecom(seed=seed, task_core=core, on_frame=on_frame)
            elif case.domain == "healthcare":
                from .simulators.healthcare import run_healthcare

                external = run_healthcare(seed=seed, task_core=core, on_frame=on_frame)
            elif case.domain == "rag":
                from .simulators.rag import run_rag

                external = run_rag(seed=seed, task_core=core, on_frame=on_frame)
            else:
                from .simulators.oil_gas import run_oil_gas

                external = run_oil_gas(seed=seed, task_core=core, on_frame=on_frame)
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
        guidance = describe_suite_guidance(checks)
        if guidance:
            row["guidance"] = guidance
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


def _workbench_cases(
    base_url: str, timeout: float, check_cancelled: Callable[[], None] | None = None
) -> list[dict[str, Any]]:
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
            _checkpoint(check_cancelled)
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
            "Set ORTHOCORE_API_KEY in your environment before running live checks (credentials are never printed)"
        )
    challenge_selections = {"challenges", "challenge-tasks", "challenge-privacy"}
    multimodal_selections = {
        "multimodal",
        "multimodal-audio",
        "multimodal-image",
        "multimodal-video",
    }
    if case_id not in {
        "all",
        "workbench",
        *challenge_selections,
        *multimodal_selections,
        *(case.id for case in CASES),
    }:
        raise ValueError(
            "Unknown case; choose all, workbench, challenges, challenge-tasks, challenge-privacy, "
            "multimodal, multimodal-audio, multimodal-image, multimodal-video, "
            "coding, tool-injection, telecom, robotics, oil-gas, healthcare, rag, email or unicode"
        )
    if case_id in multimodal_selections:
        from .multimodal_live import run_multimodal_live

        rows = run_multimodal_live(
            base_url=base_url,
            key=key,
            selection=case_id,
            seed=seed,
            repeat=repeat,
            timeout=timeout,
        )
        result = report(rows, "actual-multimodal-http-api", seed)
        mm_samples = [
            row["metrics"]["decompose_http_wall_ms"]
            for row in rows
            if "decompose_http_wall_ms" in row.get("metrics", {})
        ]
        result["measurement_boundaries"] = {
            "decompose_http_wall_ms": "Public /v1/decompose/{audio,image,video} HTTP request and JSON parse.",
            "rehydrate_http_wall_ms": "Public /v1/rehydrate/media HTTP request and SHA-256 verification.",
            "complete_modality_wall_ms": "Whole modality workflow including single-use ticket replay denial and cleanup.",
            "warmups": 0,
            "concurrency": 1,
            "repeat_applies_to": "All selected multimodal HTTP cases; fixed synthetic media.",
        }
        if mm_samples:
            result["multimodal_decompose_observations"] = percentiles(mm_samples)
        return result
    if case_id in challenge_selections:
        from .challenges import run_challenges

        result = report(
            run_challenges(
                base_url=base_url, key=key, selection=case_id, repeat=repeat, timeout=timeout
            ),
            "actual-service-semantic-challenges",
            seed,
        )
        result["measurement_boundaries"] = {
            "http_and_cleanup_wall_ms": "Workbench HTTP/full body plus cleanup, without a simulated answer.",
            "sdk_and_cleanup_wall_ms": "Official SDK decompose plus owned-ticket cleanup, including network and validation.",
            "warmups": 0,
            "concurrency": 1,
            "repeat_applies_to": "All selected challenge cases; fixed synthetic inputs.",
        }
        return result
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
            if case_id in {"all", "rag"}:
                from .rag_live import run_rag_live_http, run_rag_live_sdk

                suites.append(run_rag_live_http(base_url=base_url, key=key, timeout=timeout))
                suites.append(run_rag_live_sdk(base_url=base_url, key=key, timeout=timeout))
            if case_id in {"all", "workbench"}:
                suites.extend(_workbench_cases(base_url, timeout))
            if case_id == "all":
                from .multimodal_live import run_multimodal_live

                suites.extend(
                    run_multimodal_live(
                        base_url=base_url,
                        key=key,
                        selection="multimodal",
                        seed=seed,
                        repeat=repeat,
                        timeout=timeout,
                    )
                )
    result = report(suites, "actual-sdk-and-api", seed)
    samples = [
        suite["metrics"]["decompose_sdk_wall_ms"]
        for suite in suites
        if "decompose_sdk_wall_ms" in suite.get("metrics", {})
    ]
    mm_samples = [
        suite["metrics"]["decompose_http_wall_ms"]
        for suite in suites
        if "decompose_http_wall_ms" in suite.get("metrics", {})
    ]
    result["measurement_boundaries"] = {
        "decompose_sdk_wall_ms": "Official SDK serialization, HTTP network, server execution and response validation; no provider time.",
        "decompose_http_wall_ms": "Public /v1/decompose/{audio,image,video} HTTP request and JSON parse.",
        "local_provider_http_wall_ms": "Real loopback HTTP request to deterministic external-provider simulator.",
        "complete_scenario_wall_ms": "Whole scenario, including provider, rehydration, reuse negative control and optional industry engine.",
        "warmups": 0,
        "concurrency": 1,
        "repeat_applies_to": "SDK and multimodal HTTP cases; public workbench cases run once.",
    }
    if samples:
        result["sdk_decompose_observations"] = percentiles(samples)
    if mm_samples:
        result["multimodal_decompose_observations"] = percentiles(mm_samples)
    return result


def validate_live_credentials(
    base_url: str | None = None, key: str | None = None, *, timeout: float = 15.0
) -> tuple[str, str, float]:
    """Validate server-side live service configuration without echoing credentials."""
    if not math.isfinite(timeout) or not 1 <= timeout <= 60:
        raise ValueError("timeout must be between 1 and 60 seconds")
    resolved_base = validate_origin(
        base_url
        if base_url is not None
        else os.environ.get("ORTHOCORE_BASE_URL", "https://eigenscope.guardianai.network")
    )
    resolved_key = key if key is not None else os.environ.get("ORTHOCORE_API_KEY", "")
    if (
        not resolved_key
        or resolved_key != resolved_key.strip()
        or any(c in resolved_key for c in "\r\n")
    ):
        raise ValueError(
            "Set ORTHOCORE_API_KEY in your environment before starting live mode (credentials are never printed)"
        )
    return resolved_base, resolved_key, float(timeout)


def build_live_web_runners(
    base_url: str | None = None, key: str | None = None, *, timeout: float = 15.0
) -> tuple[str, dict[str, Callable[..., dict[str, Any]]]]:
    """Build browser scenario runners backed by live EigenScope SDK/HTTP calls."""
    resolved_base, resolved_key, resolved_timeout = validate_live_credentials(
        base_url, key, timeout=timeout
    )
    cases_by_id = {case.id: case for case in CASES}

    def execute_sdk(
        case_id: str,
        seed: int,
        on_frame: Callable[[dict[str, Any]], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        import httpx
        from eigenscope_sdk import EigenScopeClient
        from openai import OpenAI

        from .simulators.provider import ProviderServer

        _checkpoint(check_cancelled)
        with (
            ProviderServer() as server,
            OpenAI(
                base_url=server.base_url,
                api_key="local-simulator-only",
                max_retries=0,
                http_client=httpx.Client(trust_env=False),
            ) as provider,
            closing(
                make_observed_client(
                    EigenScopeClient,
                    base_url=resolved_base,
                    bearer_token=resolved_key,
                    timeout_seconds=resolved_timeout,
                )
            ) as client,
        ):
            return _sdk_case(
                cases_by_id[case_id],
                client=client,
                provider=provider,
                provider_server=server,
                seed=seed,
                on_frame=on_frame,
                check_cancelled=check_cancelled,
            )

    def make_domain_runner(case_id: str) -> Callable[..., dict[str, Any]]:
        def runner(
            seed: int = 7,
            on_frame: Callable[[dict[str, Any]], None] | None = None,
            check_cancelled: Callable[[], None] | None = None,
        ) -> dict[str, Any]:
            sdk_row = execute_sdk(case_id, seed, on_frame, check_cancelled)
            sdk_checks = [
                check
                for check in sdk_row["checks"]
                if check.get("name") != "industry_adapter_checks_pass"
            ]
            external = sdk_row.get("external_system")
            if external is None:
                from .runner import run_scenario

                _checkpoint(check_cancelled)
                external = run_scenario(case_id, seed=seed, on_frame=on_frame)
            return {
                "suite": case_id,
                "engine": f"Live EigenScope SDK + {external.get('engine', case_id)}",
                "seed": seed,
                "checks": [*sdk_checks, *external.get("checks", [])],
                "metrics": {
                    **external.get("metrics", {}),
                    "live_eigenscope": sdk_row.get("metrics", {}),
                },
                "scope": sdk_row["scope"],
            }

        return runner

    def run_live_multimodal_scenario(
        seed: int = 7,
        on_frame: Callable[[dict[str, Any]], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        from .multimodal_live import run_multimodal_live
        from .simulators.multimodal import run_multimodal

        local_res = run_multimodal(seed=seed, on_frame=on_frame)
        live_rows = run_multimodal_live(
            base_url=resolved_base,
            key=resolved_key,
            selection="multimodal",
            seed=seed,
            repeat=1,
            timeout=resolved_timeout,
            check_cancelled=check_cancelled,
        )
        live_checks: list[dict[str, Any]] = []
        live_status_by_mod: dict[str, str] = {}
        sim_t = float(local_res["metrics"].get("simulation_time_s", 2.75))
        for mod_row in live_rows:
            modality = str(mod_row.get("modality", "video_clip"))
            short = modality.split("_", 1)[0]
            passed = outcome(mod_row) == "PASS"
            status_code = mod_row.get("http_status")
            row_checks = mod_row.get("checks", [])
            unavailable = not passed and (
                status_code == 503 or any(chk.get("http_status") == 503 for chk in row_checks)
            )
            auth_required = not passed and status_code in {401, 403}
            live_status_by_mod[modality] = (
                "PASSED"
                if passed
                else "UNAVAILABLE"
                if unavailable
                else "AUTH_REQUIRED"
                if auth_required
                else "NEEDS_REVIEW"
            )
            for chk in row_checks:
                live_checks.append(
                    {
                        **chk,
                        "name": f"live_v1_{short}_{chk['name']}",
                    }
                )
            if on_frame is not None:
                sim_t = round(sim_t + 0.25, 6)
                frame_payload: dict[str, Any] = {
                    "phase": f"live-v1-{short}",
                    "modality": modality,
                    "t": sim_t,
                    "container_bytes": mod_row.get("metrics", {}).get("raw_media_bytes", 0),
                    "rejected_controls": local_res["metrics"].get("rejected_negative_controls", 0),
                    "expected_rejected_controls": 6,
                    "exact_roundtrip": any(
                        chk.get("name") == "media_bit_exact_sha256_restored"
                        and chk.get("passed") is True
                        for chk in row_checks
                    ),
                    "live_failed": not passed,
                    "live_endpoint_unavailable": unavailable,
                    "live_modality_status": dict(live_status_by_mod),
                    "active_stage": "LIVE VERIFIED"
                    if passed
                    else "SERVICE UNAVAILABLE"
                    if unavailable
                    else "CHECK API KEY"
                    if auth_required
                    else "REVIEW LIVE CHECK",
                }
                if modality == "video_clip":
                    frame_payload.update(
                        {
                            "video_frame_count": mod_row["metrics"].get("frame_count", 0),
                            "width": mod_row["metrics"].get("width", 0),
                            "height": mod_row["metrics"].get("height", 0),
                        }
                    )
                elif modality == "image_frame":
                    frame_payload.update(
                        {
                            "width": mod_row["metrics"].get("width", 0),
                            "height": mod_row["metrics"].get("height", 0),
                        }
                    )
                on_frame(frame_payload)
        return {
            **local_res,
            "engine": f"Live /v1 multimodal HTTP ({resolved_base}) + {local_res['engine']}",
            "checks": [*live_checks, *local_res["checks"]],
            "metrics": {
                **local_res["metrics"],
                "live_multimodal": [row.get("metrics", {}) for row in live_rows],
            },
        }

    def run_live_tools_scenario(
        seed: int = 7,
        on_frame: Callable[[dict[str, Any]], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        from .simulators.tools import run_tools

        local_res = run_tools(seed=seed, on_frame=on_frame)
        sdk_row = execute_sdk("tool-injection", seed, None, check_cancelled)
        combined = [*sdk_row["checks"], *local_res["checks"]]
        if on_frame is not None:
            on_frame(
                {
                    "suite": "tools",
                    "phase": "live_sanitize_tool_output",
                    "completed_checks": len(combined),
                    "passed": all(c.get("passed") is True for c in combined),
                    "observed_checks": combined,
                }
            )
        return {
            **local_res,
            "engine": f"Live EigenScope sanitize_tool_output + {local_res['engine']}",
            "checks": combined,
            "metrics": {
                **local_res.get("metrics", {}),
                "live_eigenscope": sdk_row.get("metrics", {}),
            },
        }

    def run_live_coding_scenario(
        seed: int = 7,
        on_frame: Callable[[dict[str, Any]], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        from .simulators.coding import run_coding

        local_res = run_coding(seed=seed, on_frame=on_frame)
        sdk_row = execute_sdk("coding", seed, None, check_cancelled)
        combined = [*sdk_row["checks"], *local_res["checks"]]
        if on_frame is not None:
            on_frame(
                {
                    "suite": "coding",
                    "phase": "live_sdk_coding_roundtrip",
                    "completed_checks": len(combined),
                    "passed": all(c.get("passed") is True for c in combined),
                    "observed_checks": combined,
                }
            )
        return {
            **local_res,
            "engine": f"Live EigenScope SDK + {local_res['engine']}",
            "checks": combined,
            "metrics": {
                **local_res.get("metrics", {}),
                "live_eigenscope": sdk_row.get("metrics", {}),
            },
        }

    def run_live_provider_scenario(
        seed: int = 7,
        on_frame: Callable[[dict[str, Any]], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        from .simulators.provider import run_provider

        local_res = run_provider(seed=seed, on_frame=on_frame)
        email_row = execute_sdk("email", seed, None, check_cancelled)
        unicode_row = execute_sdk("unicode", seed, None, check_cancelled)
        wb_rows = _workbench_cases(resolved_base, resolved_timeout, check_cancelled)
        live_checks: list[dict[str, Any]] = []
        for prefix, row in (("live_email", email_row), ("live_unicode", unicode_row)):
            for chk in row.get("checks", []):
                live_checks.append({**chk, "name": f"{prefix}_{chk['name']}"})
        for wb_row in wb_rows:
            short = str(wb_row["suite"]).replace("workbench:", "live_workbench_")
            for chk in wb_row.get("checks", []):
                live_checks.append({**chk, "name": f"{short}_{chk['name']}"})
        return {
            **local_res,
            "engine": f"Live EigenScope SDK & Workbench API + {local_res['engine']}",
            "checks": [*live_checks, *local_res["checks"]],
            "metrics": {
                **local_res.get("metrics", {}),
                "live_email": email_row.get("metrics", {}),
                "live_unicode": unicode_row.get("metrics", {}),
            },
        }

    def run_live_rag_scenario(
        seed: int = 7,
        on_frame: Callable[[dict[str, Any]], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        from .rag_live import run_rag_live_http, run_rag_live_sdk

        domain_res = make_domain_runner("rag")(
            seed=seed, on_frame=on_frame, check_cancelled=check_cancelled
        )
        rag_http_row = run_rag_live_http(
            base_url=resolved_base,
            key=resolved_key,
            timeout=resolved_timeout,
            check_cancelled=check_cancelled,
        )
        rag_sdk_row = run_rag_live_sdk(
            base_url=resolved_base,
            key=resolved_key,
            timeout=resolved_timeout,
            check_cancelled=check_cancelled,
        )
        return {
            **domain_res,
            "engine": f"Live /v1/rag HTTP & SDK + {domain_res['engine']}",
            "checks": [
                *domain_res["checks"],
                *rag_http_row.get("checks", []),
                *rag_sdk_row.get("checks", []),
            ],
            "metrics": {
                **domain_res.get("metrics", {}),
                "live_rag_http": rag_http_row.get("metrics", {}),
                "live_rag_sdk": rag_sdk_row.get("metrics", {}),
            },
        }

    return (
        resolved_base,
        {
            "robotics": make_domain_runner("robotics"),
            "telecom": make_domain_runner("telecom"),
            "oil-gas": make_domain_runner("oil-gas"),
            "healthcare": make_domain_runner("healthcare"),
            "multimodal": run_live_multimodal_scenario,
            "rag": run_live_rag_scenario,
            "provider": run_live_provider_scenario,
            "tools": run_live_tools_scenario,
            "coding": run_live_coding_scenario,
        },
    )
