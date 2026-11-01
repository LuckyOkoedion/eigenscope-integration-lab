"""Independent challenge oracles and runner boundaries; no hosted calls."""

import builtins
import json
from types import SimpleNamespace

import pytest

from eigenscope_lab import challenges, live
from eigenscope_lab.reporting import outcome


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


def test_published_expectations_are_finite_distinct_and_complete():
    assert len(challenges.ANSWER_CASES) == len(challenges.PRIVACY_CASES) == 9
    assert len({case.id for case in challenges.ANSWER_CASES}) == 9
    assert len({case.id for case in challenges.PRIVACY_CASES}) == 9
    for case in challenges.ANSWER_CASES:
        literal = case.expected if isinstance(case.expected, str) else json.dumps(case.expected)
        assert challenges.answer_matches(literal, case.expected)


@pytest.mark.parametrize("core", ["", " ", None, {}, [], "no retained task constraint"])
def test_empty_or_taskless_core_cannot_pass_privacy(core):
    row = {"checks": challenges.privacy_checks(challenges.PRIVACY_CASES[0], core)}
    assert outcome(row) == "FAIL"


def test_private_nfd_alias_is_detected_after_nfc_and_casefold():
    case = challenges.PRIVACY_CASES[-1]
    checks = challenges.privacy_checks(case, "Private E\u0301TOILE Client. Preserve 45 ms.")
    assert (
        next(check for check in checks if check["name"] == "explicit_private_values_absent")[
            "passed"
        ]
        is False
    )
    assert outcome({"checks": challenges.privacy_checks(case, "Preserve 45 ms.")}) == "PASS"


@pytest.mark.parametrize("value", ["compressor @ example . invalid", "compressor@example.invalid"])
def test_spaced_address_or_compacted_equivalent_is_a_leak(value):
    assert (
        outcome(
            {"checks": challenges.privacy_checks(challenges.PRIVACY_CASES[6], f"{value}. 45 ms")}
        )
        == "FAIL"
    )


class PrivacyClient:
    def __init__(self, core="Preserve 45 ms.", *, cleanup=True, failure=False):
        self.core = core
        self.cleanup = cleanup
        self.failure = failure
        self.owned_tickets = set()
        self.scrubbed = []

    def decompose(self, _prompt):
        self.owned_tickets.add("vlt_synthetic_owned_challenge")
        if self.failure:
            raise live.ServiceResponseContractError("PRIVATE_EXCEPTION_MUST_NOT_BE_EXPORTED")
        return {"status": "DECOMPOSED_OK", "task_core": self.core}

    def scrub(self, ticket):
        self.scrubbed.append(ticket)
        return {"vault_ticket_scrubbed": self.cleanup}


def test_privacy_failure_still_cleans_own_returned_mapping_and_omits_secrets():
    client = PrivacyClient(failure=True)
    row = challenges._privacy_case(challenges.PRIVACY_CASES[0], client)
    assert outcome(row) == "FAIL"
    assert client.scrubbed == ["vlt_synthetic_owned_challenge"]
    assert not client.owned_tickets
    assert "PRIVATE_EXCEPTION" not in json.dumps(row)
    assert "vlt_" not in json.dumps(row)


def test_cleanup_failure_cannot_be_a_passing_privacy_workflow():
    client = PrivacyClient(cleanup=False)
    row = challenges._privacy_case(challenges.PRIVACY_CASES[0], client)
    assert outcome(row) == "FAIL"
    assert client.owned_tickets == {"vlt_synthetic_owned_challenge"}


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
