#!/usr/bin/env python3
"""Lint the API contract: a valid OpenAPI document whose every operation is executable.

Two checks beyond schema validity, both aimed at the phase-zero stub:

1. **Every operation carries at least one example** on some response (204 excepted). The
   stub serves examples; an operation without one renders as a blank screen, which is the
   failure R3's evidence is supposed to catch.
2. **Every example validates against its declared schema**, request bodies included. This is
   the guard against a silently mis-parsed flow mapping in the YAML -- a comma that became a
   bogus key fails a required-field or additionalProperties check here rather than at demo
   time.
3. **Example names are unique within an operation**, across status codes, so that the stub's
   ``?example=<name>`` selector is unambiguous.

Runs from ./scripts/validate.sh. Exits non-zero on any finding.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

import jsonschema
from openapi_spec_validator import validate

from egpt_retail.api import spec


def _iter_operations(document: Dict[str, Any]):
    for route, item in document["paths"].items():
        for method, operation in item.items():
            if method.startswith("x-") or method == "parameters":
                continue
            yield route, method.upper(), operation


def _validate_example(document: Dict[str, Any], schema: Dict[str, Any], value: Any, where: str) -> List[str]:
    validator = jsonschema.Draft202012Validator({**schema, "components": document["components"]})
    return [
        f"{where}: {error.message[:160]} at /{'/'.join(str(p) for p in error.path)}"
        for error in list(validator.iter_errors(value))[:3]
    ]


def main() -> int:
    document = spec.bundle()
    validate(document)

    findings: List[str] = []
    operations = 0
    examples = 0
    for route, method, operation in _iter_operations(document):
        operations += 1
        has_example = False
        body = (operation.get("requestBody") or {}).get("content", {}).get("application/json")
        if body and body.get("schema"):
            for name, example in (body.get("examples") or {}).items():
                examples += 1
                findings += _validate_example(document, body["schema"], example["value"], f"{method} {route} request '{name}'")
        for code, response in operation.get("responses", {}).items():
            media = (response.get("content") or {}).get("application/json")
            if not media:
                if code == "204":
                    has_example = True
                continue
            if not media.get("schema"):
                continue
            for name, example in (media.get("examples") or {}).items():
                examples += 1
                has_example = True
                findings += _validate_example(document, media["schema"], example["value"], f"{method} {route} {code} '{name}'")
        if not has_example:
            findings.append(f"{method} {route}: no response example; the stub would serve nothing")
        seen: Dict[str, str] = {}
        for code, response in operation.get("responses", {}).items():
            media = (response.get("content") or {}).get("application/json") or {}
            for name in (media.get("examples") or {}):
                if name in seen:
                    findings.append(
                        f"{method} {route}: example name {name!r} is used on both {seen[name]} and {code}; "
                        "names must be unique per operation so ?example= is unambiguous"
                    )
                seen[name] = code

    print(f"openapi: {operations} operations, {len(document['components']['schemas'])} schemas, {examples} examples")
    if findings:
        print(f"openapi: {len(findings)} finding(s)")
        for finding in findings:
            print("  -", finding)
        return 1
    print("openapi: every operation has an example and every example validates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
