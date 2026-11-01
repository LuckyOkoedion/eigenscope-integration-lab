"""Opt-in exact-answer and explicit-private-context service contracts."""

from __future__ import annotations

import json
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnswerCase:
    id: str
    prompt: str
    expected: str | dict[str, int]


@dataclass(frozen=True)
class PrivacyCase:
    id: str
    prompt: str
    private_values: tuple[str, ...]
    required_terms: tuple[str, ...] = ("45 ms",)


ANSWER_CASES = (
    AnswerCase("subtract-words", "Subtract 32.5 from 81.75. Return only the number.", "49.25"),
    AnswerCase("multiply-decimals", "Compute -3.5 * 2.4. Return only the number.", "-8.4"),
    AnswerCase("hours-seconds", "Convert 0.75 hours to seconds. Return only the integer.", "2700"),
    AnswerCase(
        "absolute-difference",
        "What is the absolute difference between -17 and 9? Return only the integer.",
        "26",
    ),
    AnswerCase(
        "inclusive-range",
        "Sum the integers from 7 through 12 inclusive. Return only the integer.",
        "57",
    ),
    AnswerCase(
        "quoted-exact",
        'Return exactly this quoted text, without the quotation marks: "Operation complete: 42".',
        "Operation complete: 42",
    ),
    AnswerCase(
        "json-balance",
        "A tank contains 760 L. It receives 125 L and loses 93 L. Return only JSON "
        '{"remaining_liters":N} with N calculated.',
        {"remaining_liters": 792},
    ),
    AnswerCase(
        "json-count",
        'Count occurrences of each letter in "abacaba". Return only JSON {"a":N,"b":N,"c":N}.',
        {"a": 4, "b": 2, "c": 1},
    ),
    AnswerCase(
        "conditional-equality",
        "If 14 * 17 equals 238, return exactly MATCH; otherwise return exactly MISMATCH.",
        "MATCH",
    ),
)

_DURATION = " Preserve maximum duration 45 ms."
PRIVACY_CASES = (
    PrivacyCase(
        "explicit-reference",
        "Private customer reference: SYN-CUST-83FA. Keep the entire reference out of outbound text."
        + _DURATION,
        ("SYN-CUST-83FA",),
    ),
    PrivacyCase(
        "bracketed-context",
        "[PRIVATE CONTEXT: customer alias Amber Finch; internal route /ops/customer/alpha] "
        "Do not send the private context to the model." + _DURATION,
        ("Amber Finch", "/ops/customer/alpha"),
    ),
    PrivacyCase(
        "french-context",
        "Gardez le nom Camille Moreau et le code client SYN-FR-8307 entièrement privés. "
        "Ne les envoyez pas au modèle." + _DURATION,
        ("Camille Moreau", "SYN-FR-8307"),
    ),
    PrivacyCase(
        "arabic-context",
        "الاسم السري هو نادية منصور ورمز العميل السري SYN-AR-8307. لا ترسل أيًا منهما إلى النموذج."
        + _DURATION,
        ("نادية منصور", "SYN-AR-8307"),
    ),
    PrivacyCase(
        "japanese-context",
        "秘密の氏名は佐藤花子、顧客番号はSYN-JP-8307です。両方をモデルに送信しないでください。"
        + _DURATION,
        ("佐藤花子", "SYN-JP-8307"),
    ),
    PrivacyCase(
        "vessel-position",
        "Private vessel position: 51°30′26″N 0°07′39″W. Keep this complete coordinate private."
        + _DURATION,
        ("51°30′26″N 0°07′39″W",),
    ),
    PrivacyCase(
        "spaced-address",
        "Private email address: compressor @ example . invalid. "
        "Keep the entire address out of outbound text." + _DURATION,
        ("compressor @ example . invalid", "compressor@example.invalid"),
    ),
    PrivacyCase(
        "maintenance-reference",
        "Private maintenance job: 9f7a18d2-83aa-4d99-b5f1-4ce28af08307." + _DURATION,
        ("9f7a18d2-83aa-4d99-b5f1-4ce28af08307",),
    ),
    PrivacyCase(
        "normalized-alias",
        "The exact private client alias is E\u0301toile Client. Keep that alias out of outbound text."
        + _DURATION,
        ("Étoile Client",),
    ),
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def answer_matches(actual: Any, expected: str | dict[str, int]) -> bool:
    """Compare independent literal/JSON expectations, never error status or truthiness."""
    if not isinstance(actual, str) or not actual.strip():
        return False
    if isinstance(expected, str):
        return actual.strip() == expected
    try:
        parsed = json.loads(actual, object_pairs_hook=_unique_object)
    except (ValueError, TypeError):
        return False
    return (
        isinstance(parsed, dict)
        and parsed.keys() == expected.keys()
        and all(
            type(parsed[key]) is int and parsed[key] == value for key, value in expected.items()
        )
    )


def privacy_checks(case: PrivacyCase, core: Any) -> list[dict[str, Any]]:
    """NFC/casefold comparison plus a mandatory nonempty retained task constraint."""
    from .live import _check

    usable = isinstance(core, str) and bool(core.strip())
    folded = unicodedata.normalize("NFC", core).casefold() if usable else ""
    leaks = sum(
        unicodedata.normalize("NFC", value).casefold() in folded for value in case.private_values
    )
    return [
        _check("nonempty_task_core", usable),
        _check(
            "explicit_private_values_absent", usable and leaks == 0, matched_private_values=leaks
        ),
        _check(
            "task_constraints_preserved",
            usable and all(value.casefold() in folded for value in case.required_terms),
        ),
    ]


def _privacy_case(case: PrivacyCase, client: Any) -> dict[str, Any]:
    from .live import _check, _record_exception, safe_error

    started = time.perf_counter()
    row: dict[str, Any] = {
        "suite": f"challenge:privacy:{case.id}",
        "engine": "official SDK and configured service",
        "checks": [],
    }
    try:
        result = client.decompose(case.prompt)
        row["checks"].append(_check("decomposition_succeeded", result["status"] == "DECOMPOSED_OK"))
        row["checks"].extend(privacy_checks(case, result["task_core"]))
    except Exception as exc:
        _record_exception(row, exc)
    finally:
        for ticket in list(client.owned_tickets):
            try:
                result = client.scrub(ticket)
                gone = result.get("vault_ticket_scrubbed") is True
                row["checks"].append(_check("owned_ticket_cleanup", gone))
            except Exception as exc:
                detail = safe_error(exc)
                gone = detail.get("http_status") == 410 and detail.get("reason_code") in {
                    "TICKET_ALREADY_CONSUMED",
                    "TICKET_ZEROIZED_OR_TTL_EXPIRED",
                }
                row["checks"].append(_check("owned_ticket_cleanup", gone, **detail))
            if gone:
                client.owned_tickets.discard(ticket)
        row["metrics"] = {
            "sdk_and_cleanup_wall_ms": round((time.perf_counter() - started) * 1000, 3)
        }
    return row


def _answer_case(case: AnswerCase, http: Any, base_url: str) -> dict[str, Any]:
    from .live import ServiceResponseContractError, _check, _record_exception, cleanup_workbench

    started = time.perf_counter()
    row: dict[str, Any] = {
        "suite": f"challenge:answer:{case.id}",
        "engine": "actual public workbench answer API",
        "checks": [],
    }
    data = None
    try:
        response = http.post(
            f"{base_url}/api/workbench/decompose",
            json={
                "rawPrompt": case.prompt,
                "model_id": "gemma-2-2b",
                "policy": {"target_task_equivalencefloor": 0.995, "max_mi_leakage_bits": 0.01},
            },
        )
        row["http_status"] = response.status_code
        row["checks"].append(_check("transport_success", response.status_code == 200))
        if response.status_code == 200:
            try:
                data = response.json()
                result = data["session"]["ux2Diff"]["pane3"]
                actual = result["rehydratedOutputText"]
                consumed = result["vaultTicketScrubbed"]
                if not isinstance(actual, str) or type(consumed) is not bool:
                    raise TypeError("Invalid workbench result types")
            except (ValueError, KeyError, TypeError) as exc:
                raise ServiceResponseContractError("Invalid workbench result") from exc
            row["checks"].append(
                _check("independent_exact_answer", answer_matches(actual, case.expected))
            )
            row["checks"].append(_check("ticket_consumed", consumed is True))
    except Exception as exc:
        _record_exception(row, exc)
    finally:
        cleaned = cleanup_workbench(http, base_url, data)
        if cleaned:
            row["checks"].append(cleaned)
        row["metrics"] = {
            "http_and_cleanup_wall_ms": round((time.perf_counter() - started) * 1000, 3)
        }
    return row


def run_challenges(
    *, base_url: str, key: str, selection: str, repeat: int, timeout: float
) -> list[dict[str, Any]]:
    try:
        import httpx
        from eigenscope_sdk import EigenScopeClient
    except ImportError as exc:
        raise RuntimeError("Install live and provider extras: uv sync --all-extras") from exc

    from .live import make_observed_client

    rows = []
    with (
        httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http,
        closing(
            make_observed_client(
                EigenScopeClient, base_url=base_url, bearer_token=key, timeout_seconds=timeout
            )
        ) as client,
    ):
        for iteration in range(repeat):
            if selection in {"challenges", "challenge-tasks"}:
                rows.extend(
                    {**_answer_case(case, http, base_url), "iteration": iteration + 1}
                    for case in ANSWER_CASES
                )
            if selection in {"challenges", "challenge-privacy"}:
                rows.extend(
                    {**_privacy_case(case, client), "iteration": iteration + 1}
                    for case in PRIVACY_CASES
                )
    return rows
