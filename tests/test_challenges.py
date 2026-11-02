"""Independent zero-tag challenge oracles and boundaries; no hosted calls."""

import builtins
import json
import re
import unicodedata
from types import SimpleNamespace

import pytest

from eigenscope_lab import challenges, live
from eigenscope_lab.reporting import outcome


def clean_core(case):
    return " ".join((*case.required_terms, *case.format_terms[:1], *case.task_terms[:1]))


@pytest.mark.parametrize("missing", ["httpx", "eigenscope_sdk"])
def test_missing_live_dependencies_have_actionable_setup_error(monkeypatch, missing):
    original = builtins.__import__

    def import_module(name, *args, **kwargs):
        if name == missing:
            raise ModuleNotFoundError("Synthetic missing optional dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_module)
    with pytest.raises(RuntimeError, match="uv sync --all-extras"):
        challenges.run_challenges(
            base_url="https://service.example.invalid",
            key="SYNTHETIC_NEVER_SENT",
            selection="challenges",
            repeat=1,
            timeout=5,
        )


@pytest.mark.parametrize(
    "actual,expected,passed",
    [
        ("49.25", "49.25", True),
        ("The answer is 49.25", "49.25", False),
        ("", "49.25", False),
        (None, "49.25", False),
        ("Operation complete: 42", "Operation complete: 42", True),
        ('{"remaining_liters":792}', {"remaining_liters": 792}, True),
        ('{"b":2,"a":4,"c":1}', {"a": 4, "b": 2, "c": 1}, True),
        ('{"a":4,"a":4}', {"a": 4}, False),
        ('{"a":true}', {"a": 1}, False),
        ('{"a":"4"}', {"a": 4}, False),
        ('{"a":4,"extra":0}', {"a": 4}, False),
        ('{"a":NaN}', {"a": 4}, False),
        ('{"a":Infinity}', {"a": 4}, False),
        ('{"error":"unavailable"}', {"a": 4}, False),
    ],
)
def test_answer_oracle(actual, expected, passed):
    assert challenges.answer_matches(actual, expected) is passed


def test_published_expectations_are_distinct_and_complete():
    assert len(challenges.ANSWER_CASES) == len(challenges.PRIVACY_CASES) == 9
    assert len({case.id for case in challenges.ANSWER_CASES}) == 9
    assert len({case.id for case in challenges.PRIVACY_CASES}) == 9
    for case in challenges.ANSWER_CASES:
        literal = case.expected if isinstance(case.expected, str) else json.dumps(case.expected)
        assert challenges.answer_matches(literal, case.expected)


@pytest.mark.parametrize("index", range(9))
def test_primary_prompts_use_natural_entities_without_sensitivity_cues(index):
    case = challenges.PRIVACY_CASES[index]
    challenges.validate_untagged_prompt(case.prompt)
    assert not re.search(r"\b(?:private|confidential|secret|hidden)\b", case.prompt, re.I)
    compact_prompt = "".join(unicodedata.normalize("NFC", case.prompt).casefold().split())
    for value in case.private_values:
        compact_value = "".join(unicodedata.normalize("NFC", value).casefold().split())
        assert compact_value in compact_prompt
    assert case.format_terms
    assert case.task_terms
    assert outcome({"checks": challenges.privacy_checks(case, clean_core(case))}) == "PASS"


# These are rejected authoring inputs, never prompts containing labeled entities.
@pytest.mark.parametrize(
    "cue",
    [
        "private",
        "confidential",
        "secret",
        "hidden",
        "privés",
        "السري",
        "秘密",
        "redact",
        "mask",
        "scrub",
        "do\nnot send",
        "do\t not include",
        "Ｐｒｉｖａｔｅ",
        "remove account",
    ],
)
def test_sensitivity_cue_is_rejected_before_fixture_can_be_registered(cue):
    with pytest.raises(ValueError):
        challenges.validate_untagged_prompt(cue)
    with pytest.raises(ValueError):
        challenges.PrivacyCase("rejected-authoring-input", cue, ())


@pytest.mark.parametrize(
    "cue",
    [
        "private",
        "confidential",
        "secret",
        "hidden",
        "privés",
        "السري",
        "秘密",
        "redact",
        "mask",
        "scrub",
        "do\nnot send",
        "do\t not include",
        "Ｐｒｉｖａｔｅ",
        "remove account",
    ],
)
def test_case_execution_rejects_untagged_contract_violation_before_api_call(cue):
    case = SimpleNamespace(id="rejected-authoring-input", prompt=cue)

    class ForbiddenClient:
        def decompose(self, _prompt):
            pytest.fail("The fixture guard must reject this input before any SDK call")

    with pytest.raises(ValueError):
        challenges._privacy_case(case, ForbiddenClient())


@pytest.mark.parametrize("core", ["", " ", None, {}, [], "no retained task constraint"])
def test_empty_or_taskless_core_cannot_pass_privacy(core):
    assert (
        outcome({"checks": challenges.privacy_checks(challenges.PRIVACY_CASES[0], core)}) == "FAIL"
    )


def test_retaining_only_public_values_cannot_drop_the_requested_format_task():
    case = challenges.PRIVACY_CASES[0]
    core = " ".join(case.required_terms)
    checks = challenges.privacy_checks(case, core)
    assert next(check for check in checks if check["name"] == "sensitive_values_absent")["passed"]
    assert outcome({"checks": checks}) == "FAIL"


@pytest.mark.parametrize("index", range(9))
def test_common_constraint_only_output_cannot_discard_each_cases_actual_task(index):
    case = challenges.PRIVACY_CASES[index]
    checks = {
        check["name"]: check
        for check in challenges.privacy_checks(case, "three bullet points Python HTTP 200 45 ms")
    }
    assert checks["sensitive_values_absent"]["passed"] is True
    assert checks["task_constraints_preserved"]["passed"] is True
    assert checks["task_format_instruction_preserved"]["passed"] is True
    assert checks["task_intent_preserved"]["passed"] is False
    assert outcome({"checks": list(checks.values())}) == "FAIL"


@pytest.mark.parametrize(
    "index,core",
    [
        (
            0,
            "Draft an onboarding update for [REF_1] in three bullet points. Include Python, HTTP 200 and 45 ms.",
        ),
        (
            1,
            "Draft an onboarding update for [CLIENT_1] using endpoint [ROUTE_1]. Use three bullet points and retain Python, HTTP 200 and 45 ms.",
        ),
        (
            2,
            "Rédigez un compte rendu d’intégration pour [CLIENT_1] en trois puces avec Python, HTTP 200 et 45 ms.",
        ),
        (
            3,
            "أعد ملخص انضمام من ثلاث نقاط للعميلة [CLIENT_1] باستخدام Python وHTTP 200 ومدة قصوى 45 ms.",
        ),
        (
            4,
            "顧客[CLIENT_1]のオンボーディング状況を3つの箇条書きにまとめてください。Python、HTTP 200、45 msを含めてください。",
        ),
        (
            5,
            "Draft three bullet points for the vessel at [LOCATION_1]. Include Python, HTTP 200 and 45 ms in the telemetry summary.",
        ),
        (
            6,
            "Draft a three bullet point inspection update for [CONTACT_1]. Include Python, HTTP 200 and 45 ms.",
        ),
        (
            7,
            "Summarize maintenance job [JOB_1] in three bullet points. Include Python, HTTP 200 and 45 ms.",
        ),
        (
            8,
            "Draft a three bullet point onboarding update for [CLIENT_1]. Include Python, HTTP 200 and 45 ms.",
        ),
    ],
)
def test_natural_retained_task_intent_cores_preserve_the_independent_contract(index, core):
    checks = challenges.privacy_checks(challenges.PRIVACY_CASES[index], core)
    assert (
        next(check for check in checks if check["name"] == "task_intent_preserved")["passed"]
        is True
    )
    assert outcome({"checks": checks}) == "PASS"


def test_untagged_nfd_alias_is_detected_after_nfc_and_casefold():
    case = challenges.PRIVACY_CASES[-1]
    checks = challenges.privacy_checks(case, "E\u0301TOILE Client. Preserve 45 ms.")
    assert (
        next(check for check in checks if check["name"] == "sensitive_values_absent")["passed"]
        is False
    )
    assert outcome({"checks": challenges.privacy_checks(case, clean_core(case))}) == "PASS"


@pytest.mark.parametrize("value", ["compressor @ example . invalid", "compressor@example.invalid"])
def test_spaced_address_or_compacted_equivalent_is_a_leak(value):
    case = challenges.PRIVACY_CASES[6]
    assert (
        outcome({"checks": challenges.privacy_checks(case, f"{value}. {clean_core(case)}")})
        == "FAIL"
    )


class PrivacyClient:
    def __init__(self, core=None, *, cleanup=True, failure=False):
        self.core = clean_core(challenges.PRIVACY_CASES[0]) if core is None else core
        self.cleanup = cleanup
        self.failure = failure
        self.owned_tickets = set()
        self.scrubbed = []

    def decompose(self, _prompt):
        self.owned_tickets.add("vlt_synthetic_owned_challenge")
        if self.failure:
            raise live.ServiceResponseContractError("SYNTHETIC_EXCEPTION_DETAIL")
        return {"status": "DECOMPOSED_OK", "task_core": self.core}

    def scrub(self, ticket):
        self.scrubbed.append(ticket)
        return {"vault_ticket_scrubbed": self.cleanup}


def test_decomposition_failure_still_cleans_own_returned_mapping_and_omits_details():
    client = PrivacyClient(failure=True)
    row = challenges._privacy_case(challenges.PRIVACY_CASES[0], client)
    assert outcome(row) == "FAIL"
    assert client.scrubbed == ["vlt_synthetic_owned_challenge"]
    assert not client.owned_tickets
    assert "SYNTHETIC_EXCEPTION_DETAIL" not in json.dumps(row)
    assert "vlt_" not in json.dumps(row)


def test_cleanup_failure_cannot_be_a_passing_privacy_workflow():
    client = PrivacyClient(cleanup=False)
    row = challenges._privacy_case(challenges.PRIVACY_CASES[0], client)
    assert outcome(row) == "FAIL"
    assert client.owned_tickets == {"vlt_synthetic_owned_challenge"}


@pytest.mark.parametrize("mode", ["clean", "leak", "refusal", "empty"])
def test_natural_case_outcome_requires_real_completion_absence_and_task_preservation(mode):
    case = challenges.PRIVACY_CASES[0]

    class ControlledClient(PrivacyClient):
        def decompose(self, prompt):
            assert prompt == case.prompt
            self.owned_tickets.add("vlt_synthetic_owned_challenge")
            if mode == "refusal":
                error = live.ServiceResponseContractError("SYNTHETIC_REFUSAL_DETAIL")
                error.http_status = 422
                raise error
            core = clean_core(case) if mode == "clean" else prompt if mode == "leak" else ""
            return {"status": "DECOMPOSED_OK", "task_core": core}

    client = ControlledClient()
    row = challenges._privacy_case(case, client)
    assert outcome(row) == ("PASS" if mode == "clean" else "FAIL")
    assert not client.owned_tickets
    assert "SYNTHETIC_REFUSAL_DETAIL" not in json.dumps(row)
    assert all(value not in json.dumps(row) for value in case.private_values)


class WorkbenchHTTP:
    def __init__(self, answer="49.25", *, status=200, consumed=True, malformed=False):
        self.answer = answer
        self.status = status
        self.consumed = consumed
        self.malformed = malformed
        self.calls = []

    def post(self, url, *, json):
        self.calls.append((url, json))
        data = (
            {}
            if self.malformed
            else {
                "session": {
                    "ux2Diff": {
                        "pane2": {},
                        "pane3": {
                            "rehydratedOutputText": self.answer,
                            "vaultTicketScrubbed": self.consumed,
                        },
                    }
                }
            }
        )
        return SimpleNamespace(status_code=self.status, json=lambda: data)


@pytest.mark.parametrize(
    "options,expected",
    [
        ({}, "PASS"),
        ({"answer": "0"}, "FAIL"),
        ({"answer": ""}, "FAIL"),
        ({"status": 503}, "FAIL"),
        ({"consumed": False}, "FAIL"),
        ({"consumed": "true"}, "FAIL"),
        ({"malformed": True}, "FAIL"),
    ],
)
def test_actual_answer_and_consumption_are_required(options, expected):
    http = WorkbenchHTTP(**options)
    row = challenges._answer_case(
        challenges.ANSWER_CASES[0], http, "https://service.example.invalid"
    )
    assert outcome(row) == expected
    assert len(http.calls) == 1
    assert "rehydratedOutputText" not in json.dumps(row)


@pytest.mark.parametrize("selection", ["challenges", "challenge-tasks", "challenge-privacy"])
def test_explicit_selection_does_not_initialize_provider(monkeypatch, selection):
    monkeypatch.setenv("ORTHOCORE_API_KEY", "SYNTHETIC_KEY_MUST_NOT_BE_EXPORTED")
    monkeypatch.setenv("ORTHOCORE_BASE_URL", "https://service.example.invalid")
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return [{"suite": "synthetic-challenge", "checks": [{"name": "oracle", "passed": True}]}]

    monkeypatch.setattr(challenges, "run_challenges", run)
    result = live.run_live(case_id=selection, repeat=2)
    assert calls[0]["selection"] == selection
    assert calls[0]["repeat"] == 2
    assert result["summary"] == {"PASS": 1, "FAIL": 0, "BLOCKED": 0, "ERROR": 0}
    assert "SYNTHETIC_KEY" not in json.dumps(result)


@pytest.mark.parametrize(
    "selection,expected_per_iteration",
    [("challenges", 18), ("challenge-tasks", 9), ("challenge-privacy", 9)],
)
def test_selected_cases_repeat_with_distinct_ids_and_close_resources(
    monkeypatch, selection, expected_per_iteration
):
    import httpx

    resources = []

    class Resource:
        closed = False

        def __init__(self, **_kwargs):
            resources.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            self.closed = True

    monkeypatch.setattr(httpx, "Client", Resource)
    monkeypatch.setattr(live, "make_observed_client", lambda *_args, **_kwargs: Resource())
    monkeypatch.setattr(
        challenges, "_answer_case", lambda case, *_args: {"suite": "answer:" + case.id}
    )
    monkeypatch.setattr(
        challenges, "_privacy_case", lambda case, *_args: {"suite": "privacy:" + case.id}
    )
    rows = challenges.run_challenges(
        base_url="https://service.example.invalid",
        key="SYNTHETIC_NEVER_SENT",
        selection=selection,
        repeat=2,
        timeout=5,
    )
    assert len(rows) == 2 * expected_per_iteration
    for iteration in (1, 2):
        ids = [row["suite"] for row in rows if row["iteration"] == iteration]
        assert len(set(ids)) == expected_per_iteration
    assert len(resources) == 2 and all(resource.closed for resource in resources)
