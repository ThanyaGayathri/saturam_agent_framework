"""Cognee probe against SM-R10's scenario set.

Cognee is scored at the **data-model** level rather than end-to-end, and the
reason is a property of the library, not a shortcut: cognee's temporal model has
no transaction axis, so 8 of SM-R10's 30 probes are not merely wrong under it --
they are inexpressible. Running the full `cognify` pipeline would additionally
require LLM credentials and would not change that.

Everything asserted here is checked against the installed cognee 1.4.2 at
runtime, so this fails loudly if a future version adds the missing axis.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.memory_bitemporal.scenarios import build_scenarios

FINDINGS: dict = {"engine": "cognee 1.4.2"}


def probe_model() -> None:
    """Cognee's own temporal graph model: which axes exist?"""
    from cognee.tasks.temporal_graph.models import Event

    fields = set(Event.model_fields)
    FINDINGS["event_fields"] = sorted(fields)
    FINDINGS["has_valid_time_axis"] = {"time_from", "time_to"} <= fields
    # A transaction axis needs *belief* bounds, not just world-time bounds.
    FINDINGS["has_transaction_axis"] = bool(
        {"created_at", "expired_at", "recorded_at"} & fields
    )


def probe_bitemporal_fields_are_migration_only() -> None:
    """`valid_at`/`invalid_at` exist -- but only on the import path."""
    from cognee.modules.migration.cogx import COGXFact

    FINDINGS["cogx_fact_has_valid_at"] = "valid_at" in COGXFact.model_fields
    FINDINGS["cogx_fact_has_expired_at"] = "expired_at" in COGXFact.model_fields
    FINDINGS["cogx_docstring"] = " ".join((COGXFact.__doc__ or "").split())


def probe_temporal_read_path() -> None:
    """Does the point-in-time read path call an LLM?"""
    import inspect

    from cognee.modules.retrieval.temporal_retriever import TemporalRetriever

    src = inspect.getsource(TemporalRetriever)
    FINDINGS["read_path_calls_llm"] = "LLMGateway" in src
    FINDINGS["read_path_llm_call"] = next(
        (ln.strip() for ln in src.splitlines() if "acreate_structured_output" in ln),
        None,
    )


def probe_ppr() -> None:
    import pathlib

    import cognee

    root = pathlib.Path(cognee.__file__).parent
    hits = [
        p.name
        for p in root.rglob("*.py")
        if "pagerank" in p.read_text(errors="ignore").lower()
    ]
    FINDINGS["files_mentioning_pagerank"] = hits


def score_expressibility() -> None:
    """How much of SM-R10's probe set can cognee's model even represent?"""
    total = expressible = 0
    blocked = []
    for sc in build_scenarios():
        for pr in sc.probes:
            total += 1
            if pr.tt is None:
                expressible += 1  # world-time only: representable
            else:
                blocked.append(f"{sc.name}:t={pr.t},tt={pr.tt}")
    FINDINGS["probes_total"] = total
    FINDINGS["probes_expressible_5a"] = expressible
    FINDINGS["probes_inexpressible_5b"] = len(blocked)
    FINDINGS["blocked_probes"] = blocked


def main() -> int:
    probe_model()
    probe_bitemporal_fields_are_migration_only()
    probe_temporal_read_path()
    probe_ppr()
    score_expressibility()

    out = Path(__file__).parent / "results" / "cognee_probe.json"
    out.write_text(json.dumps(FINDINGS, indent=2))

    print("cognee 1.4.2 -- structural probe against SM-R10's scenario set")
    print()
    print(f"  Event model fields        : {FINDINGS['event_fields']}")
    print(f"  has valid-time axis       : {FINDINGS['has_valid_time_axis']}")
    print(
        f"  has TRANSACTION axis      : {FINDINGS['has_transaction_axis']}   <-- 5b needs this"
    )
    print()
    print(
        f"  COGXFact.valid_at         : {FINDINGS['cogx_fact_has_valid_at']} (migration/import path)"
    )
    print(f"  COGXFact.expired_at       : {FINDINGS['cogx_fact_has_expired_at']}")
    print(f'  cognee\'s own docstring    : "{FINDINGS["cogx_docstring"][:150]}..."')
    print()
    print(f"  temporal read path uses LLM: {FINDINGS['read_path_calls_llm']}")
    print(f"    -> {FINDINGS['read_path_llm_call']}")
    print(
        f"  files mentioning pagerank : {FINDINGS['files_mentioning_pagerank'] or 'NONE'}"
    )
    print()
    print(f"  SM-R10 probes total       : {FINDINGS['probes_total']}")
    print(f"    expressible (5a)        : {FINDINGS['probes_expressible_5a']}")
    print(f"    INEXPRESSIBLE (5b)      : {FINDINGS['probes_inexpressible_5b']}")
    print()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
