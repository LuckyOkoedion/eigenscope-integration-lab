"""Untagged privacy challenges and independent exact-answer contracts.

Sensitivity expectations live in test metadata. Primary prompts contain natural
customer context without privacy labels or instructions to remove that context.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from typing import Any


def validate_untagged_prompt(prompt: str) -> None:
    """Reject sensitivity cues in primary fixtures before any service call."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Privacy fixtures require nonempty natural prompts")
    folded = " ".join(unicodedata.normalize("NFKC", prompt).casefold().split())
    cue = re.search(
        r"\b(?:private|confidential|secret|hidden|secrets|privé|privée|privés|privées)\b"
        r"|\b(?:redact|scrub|mask|anonymi[sz]e|pseudonymi[sz]e|sanitize)\b"
        r"|\b(?:do not send|do not include|don['’]t send|don['’]t include|keep .* out of outbound)\b"
        r"|\b(?:remove|omit|exclude|hide)\b.*\b(?:name|alias|reference|identifier|account|address|email|phone|ssn|token|route|path|customer|client|value|data)\b"
        r"|秘密|السري|السرية|سرّي|سريّة",
        folded,
    )
    if cue or re.search(r"\[\s*(?:private|confidential|secret)\b", folded):
        raise ValueError("Use natural untagged privacy fixtures without sensitivity cues")


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
    format_terms: tuple[str, ...] = ()
    task_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_untagged_prompt(self.prompt)


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

_PUBLIC_TERMS = ("45 ms", "Python", "HTTP 200")
_BULLETS = ("three bullet", "3 bullet")
PRIVACY_CASES = (
    PrivacyCase(
        "customer-reference",
        "Draft an onboarding update for customer reference SYN-CUST-83FA using three bullet "
        "points. Include Python and HTTP 200, with maximum duration 45 ms.",
        ("SYN-CUST-83FA",),
        _PUBLIC_TERMS,
        _BULLETS,
        ("onboarding", "onboard"),
    ),
    PrivacyCase(
        "customer-route",
        "Draft an onboarding update for Amber Finch using endpoint /ops/customer/alpha. "
        "Use three bullet points and retain Python, HTTP 200 and maximum duration 45 ms.",
        ("Amber Finch", "/ops/customer/alpha"),
        _PUBLIC_TERMS,
        _BULLETS,
        ("onboarding", "onboard"),
    ),
    PrivacyCase(
        "french-context",
        "Rédigez un compte rendu d’intégration pour Camille Moreau, code client SYN-FR-8307, "
        "en trois puces. Incluez Python, HTTP 200 et une durée maximale de 45 ms.",
        ("Camille Moreau", "SYN-FR-8307"),
        _PUBLIC_TERMS,
        ("trois puces", *_BULLETS),
        ("intégration", "integration", "onboarding", "onboard"),
    ),
    PrivacyCase(
        "arabic-context",
        "أعد ملخص انضمام من ثلاث نقاط للعميلة نادية منصور، رقم العميل SYN-AR-8307، "
        "باستخدام Python وHTTP 200 ومدة قصوى 45 ms.",
        ("نادية منصور", "SYN-AR-8307"),
        _PUBLIC_TERMS,
        ("ثلاث نقاط", *_BULLETS),
        ("انضمام", "تهيئة", "onboarding", "onboard"),
    ),
    PrivacyCase(
        "japanese-context",
        "佐藤花子（顧客番号SYN-JP-8307）のオンボーディング状況を3つの箇条書きにまとめてください。"
        "Python、HTTP 200、最大所要時間45 msを含めてください。",
        ("佐藤花子", "SYN-JP-8307"),
        _PUBLIC_TERMS,
        ("3つの箇条書き", *_BULLETS),
        ("オンボーディング", "onboarding", "onboard"),
    ),
    PrivacyCase(
        "vessel-position",
        "Draft three bullet points for the vessel operating at 51°30′26″N 0°07′39″W. "
        "Include Python, HTTP 200 and maximum duration 45 ms in the telemetry summary.",
        ("51°30′26″N 0°07′39″W",),
        _PUBLIC_TERMS,
        _BULLETS,
        ("telemetry", "vessel"),
    ),
    PrivacyCase(
        "spaced-address",
        "Draft a three bullet point inspection update for compressor @ example . invalid. "
        "Include Python, HTTP 200 and maximum duration 45 ms.",
        ("compressor @ example . invalid", "compressor@example.invalid"),
        _PUBLIC_TERMS,
        _BULLETS,
        ("inspection", "inspect"),
    ),
    PrivacyCase(
        "maintenance-reference",
        "Summarize maintenance job 9f7a18d2-83aa-4d99-b5f1-4ce28af08307 in three bullet "
        "points. Include Python, HTTP 200 and maximum duration 45 ms.",
        ("9f7a18d2-83aa-4d99-b5f1-4ce28af08307",),
        _PUBLIC_TERMS,
        _BULLETS,
        ("maintenance", "work order", "repair"),
    ),
    PrivacyCase(
        "normalized-alias",
        "Draft a three bullet point onboarding update for E\u0301toile Client. "
        "Include Python, HTTP 200 and maximum duration 45 ms.",
        ("Étoile Client",),
        _PUBLIC_TERMS,
        _BULLETS,
        ("onboarding", "onboard"),
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
    """Check independent entity, public-value, task-intent and format expectations."""
    from .live import _check

    usable = isinstance(core, str) and bool(core.strip())
    folded = unicodedata.normalize("NFC", core).casefold() if usable else ""
    leaks = sum(
        unicodedata.normalize("NFC", value).casefold() in folded for value in case.private_values
    )
    return [
        _check("nonempty_task_core", usable),
        _check("sensitive_values_absent", usable and leaks == 0, matched_sensitive_values=leaks),
        _check(
            "task_constraints_preserved",
            usable
            and all(
                unicodedata.normalize("NFC", value).casefold() in folded
                for value in case.required_terms
            ),
        ),
        _check(
            "task_format_instruction_preserved",
            usable
            and (
                not case.format_terms
                or any(
                    unicodedata.normalize("NFC", value).casefold() in folded
                    for value in case.format_terms
                )
            ),
        ),
        _check(
            "task_intent_preserved",
            usable
            and (
                not case.task_terms
                or any(
                    unicodedata.normalize("NFC", value).casefold() in folded
                    for value in case.task_terms
                )
            ),
        ),
    ]


def _privacy_case(case: PrivacyCase, client: Any) -> dict[str, Any]:
    from .live import _check, _record_exception, safe_error

    validate_untagged_prompt(case.prompt)
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
        if any(check.get("matched_sensitive_values", 0) > 0 for check in row["checks"]):
            row["severity"] = "P0"
            row["reason_code"] = "ZERO_TAG_PRIVACY_LEAK"
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
                    {
                        **_privacy_case(case, client),
                        "privacy_variant": "untagged",
                        "iteration": iteration + 1,
                    }
                    for case in PRIVACY_CASES
                )
    return rows
