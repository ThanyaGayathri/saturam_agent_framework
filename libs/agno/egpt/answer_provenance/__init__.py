"""The answer-provenance record (R7, G4): what every answer writes.

Distinct from ``egpt.memory.consolidation``'s ``provenance_report`` -- that tracks which
episodes became which facts during memory consolidation, and (per the gap this package closes)
"has nothing to do with citing an answer." This package is the framework half of R7's split
(``docs/plan/delivery-plan.md``'s "Where the code lives" table): the data model and storage are
framework primitives because every deployment needs an answer it can trace, while the executor
that assembles and writes one from a running workflow (``record_provenance``) is retail code in
``product/egpt_retail/executors/provenance.py``.
"""

from egpt.answer_provenance.ddl import PROVENANCE_TABLE, aapply_provenance_ddl, apply_provenance_ddl
from egpt.answer_provenance.record import MODEL_TIERS, ProvenanceRecord
from egpt.answer_provenance.store import (
    alist_provenance_by_use_case,
    aread_provenance,
    awrite_provenance,
    list_provenance_by_use_case,
    read_provenance,
    write_provenance,
)

__all__ = [
    "MODEL_TIERS",
    "PROVENANCE_TABLE",
    "ProvenanceRecord",
    "aapply_provenance_ddl",
    "alist_provenance_by_use_case",
    "apply_provenance_ddl",
    "aread_provenance",
    "awrite_provenance",
    "list_provenance_by_use_case",
    "read_provenance",
    "write_provenance",
]
