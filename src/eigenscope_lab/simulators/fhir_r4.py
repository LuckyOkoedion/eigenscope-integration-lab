"""Offline resource validation using HL7's complete FHIR R4 JSON schema."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from functools import lru_cache
from importlib.resources import files
from typing import Any

SCHEMA_SHA256 = "75e5560da3cf503895a44c8ca7af17a83b4cca6c2cb5ba1883d2aec0d1cb5ac6"


@lru_cache(maxsize=1)
def _validators() -> dict[str, Any]:
    try:
        from jsonschema import Draft6Validator
    except ImportError as exc:
        raise RuntimeError("Install the healthcare extra: pip install -e '.[healthcare]'") from exc
    raw = files("eigenscope_lab").joinpath("assets/fhir/fhir-r4.schema.json.zip").read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA256:
        raise RuntimeError("FHIR R4 schema asset integrity check failed")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        schema = json.loads(archive.read("fhir.schema.json"))
    return {
        kind: Draft6Validator(
            {"$ref": f"#/definitions/{kind}", "definitions": schema["definitions"]}
        )
        for kind in ("Observation", "MedicationRequest", "OperationOutcome")
    }


def validate_fhir_r4_resource(resource: Any) -> None:
    """Validate the standard resource shape; scenario business rules apply separately."""
    if (
        not isinstance(resource, dict)
        or not isinstance(resource.get("resourceType"), str)
        or resource["resourceType"] not in _validators()
    ):
        raise ValueError("Unsupported FHIR resource type")
    json.dumps(resource, allow_nan=False)
    validator = _validators()[resource["resourceType"]]
    errors = list(validator.iter_errors(resource))
    if errors:
        raise ValueError("Resource does not match the HL7 FHIR R4 JSON schema")
