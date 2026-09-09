"""The warehouse: landing, the five silver entities, and the quality gates (R4).

Three stages, and the boundary between the first two is a contract boundary. Stage 0 Raw is
Lane C's -- immutable NDJSON in object storage, governed by
``contracts/LANDING_CONTRACT.md``. Stage 1 Landing loads those files into the warehouse
as-is, unparsed, with ingest metadata. Stage 2 Silver types, deduplicates and conforms them
into the five entities Lane K's harvest (K4) reads and R5's gold materialises from.

**The transformations are SQL, not Python.** Every conformance rule and every quality
measurement lives in a file under ``sql/``, executed inside the warehouse (D6), because the
standing decision is that they must stay inspectable and hand-overable. This package owns
orchestration -- reading storage, executing the statements, reading back the counts -- and
nothing that decides what a row means.

See ``LOAD_CONTRACT.md`` for the rules the SQL implements and the findings it surfaced.
"""

from __future__ import annotations

__all__: list[str] = []
