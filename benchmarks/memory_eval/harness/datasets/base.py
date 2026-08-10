"""Loader registry + fixture path resolution.

A ``LoaderSpec`` binds a benchmark name to its three callables (download / build
/ fixture). The registry (``LOADERS``) is the single place the runner and CLI
discover benchmarks, so adding a new set is one entry, not edits at call sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from harness.schemas import Benchmark, load_unified

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures"


def fixture_path(name: str) -> Path:
    return _FIXTURE_DIR / f"{name}.sample.json"


def load_fixture(name: str) -> Benchmark:
    """Load a benchmark's committed, format-faithful synthetic mini-set.

    Fixtures are stored in the unified schema (see schemas.load_unified); the
    real-format parsers in each loader are exercised by their own raw sample
    files under fixtures/raw/ so both paths are covered offline.
    """
    bench = load_unified(fixture_path(name))
    return bench


@dataclass(frozen=True)
class LoaderSpec:
    """How to obtain one benchmark, three ways (download / build / fixture)."""

    name: str
    description: str
    # Fetch the real corpus into the gitignored raw dir; returns the raw path.
    download: Optional[Callable[[], Path]]
    # Parse a raw-format file (real download OR a committed raw sample) -> Benchmark.
    build: Optional[Callable[[Path], Benchmark]]
    # Load the committed unified-schema fixture (offline, CI).
    fixture: Callable[[], Benchmark]
    license_note: str


def _spec(name: str, description: str, license_note: str) -> LoaderSpec:
    """Build a spec, wiring download/build lazily to avoid import cycles."""

    def _fixture() -> Benchmark:
        return load_fixture(name)

    def _download() -> Path:
        mod = __import__(f"harness.datasets.{name}", fromlist=["download"])
        return mod.download()

    def _build(path: Path) -> Benchmark:
        mod = __import__(f"harness.datasets.{name}", fromlist=["build"])
        return mod.build(path)

    return LoaderSpec(
        name=name,
        description=description,
        download=_download,
        build=_build,
        fixture=_fixture,
        license_note=license_note,
    )


# The registry. Adding a benchmark = one entry here + a module of the same name.
LOADERS: Dict[str, LoaderSpec] = {
    "longmemeval": _spec(
        "longmemeval",
        "LongMemEval: 500 curated long-term-memory questions over 5 abilities "
        "(info extraction, multi-session, temporal reasoning, knowledge update, "
        "abstention). Headline benchmark per the research.",
        "Source: github.com/xiaowu0162/LongMemEval (data via HuggingFace/Drive). "
        "Custom/research license -- NOT redistributed here; fetched on demand.",
    ),
    "locomo": _spec(
        "locomo",
        "LOCOMO: 10 very long multi-session dialogues with QA probes "
        "(single-hop, multi-hop, temporal, open-domain, adversarial). External "
        "validity for QA + retrieval metrics.",
        "Source: github.com/snap-research/locomo (locomo10.json). "
        "Research use -- NOT redistributed here; fetched on demand.",
    ),
    "dmr": _spec(
        "dmr",
        "DMR (Deep Memory Retrieval, MemGPT/Letta): retrieve a single fact from a "
        "prior session. Near-saturated -- included only as a low-value sanity "
        "baseline / cross-check.",
        "Source: MemGPT/Letta (github.com/cpacker/MemGPT). "
        "Apache-2.0 upstream -- NOT redistributed here; fetched on demand.",
    ),
    "custom_temporal": _spec(
        "custom_temporal",
        "Custom bi-temporal + decay probe set: as-of-T questions whose answer "
        "changes over time, and decay probes where a stale/low-salience memory "
        "should be de-ranked. Covers behavior the public sets do NOT isolate.",
        "Authored in-house (see findings/01). SINGLE-AUTHORED -- a data-quality "
        "caveat; cross-checked against LongMemEval temporal/update categories.",
    ),
}


def list_benchmarks() -> List[str]:
    return sorted(LOADERS)


def get_loader(name: str) -> LoaderSpec:
    if name not in LOADERS:
        raise KeyError(f"unknown benchmark {name!r}; choices: {list_benchmarks()}")
    return LOADERS[name]
