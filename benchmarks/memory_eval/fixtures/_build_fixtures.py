"""Regenerate the unified-schema fixtures from the raw-format samples.

The committed ``<name>.sample.json`` files (loaded by ``load_fixture`` for CI) are
the *output* of each benchmark's real-format parser run on the tiny raw samples
under ``fixtures/raw/``. Keeping them as generated output proves the parser and
the fixture agree. ``custom_temporal`` is authored directly in the unified schema
(its raw format IS the unified schema), so it is not regenerated here.

Run from the harness root so ``harness`` is importable:
    cd benchmarks/memory_eval && python fixtures/_build_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from harness.datasets import dmr, locomo, longmemeval  # noqa: E402
from harness.schemas import to_json  # noqa: E402

_RAW = _ROOT / "fixtures" / "raw"
_OUT = _ROOT / "fixtures"


def main() -> None:
    jobs = [
        ("longmemeval", longmemeval.build(_RAW / "longmemeval_raw.json")),
        ("locomo", locomo.build(_RAW / "locomo_raw.json")),
        ("dmr", dmr.build(_RAW / "dmr_raw.json")),
    ]
    for name, bench in jobs:
        bench.validate()
        dest = _OUT / f"{name}.sample.json"
        dest.write_text(to_json(bench) + "\n")
        print(f"wrote {dest.relative_to(_ROOT)}  ({len(bench.sessions)} sessions, {len(bench.probes)} probes)")


if __name__ == "__main__":
    main()
