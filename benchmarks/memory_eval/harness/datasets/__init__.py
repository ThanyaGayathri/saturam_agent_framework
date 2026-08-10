"""Dataset loaders: normalize each benchmark's real corpus into a ``Benchmark``.

Every loader exposes the same trio so they are interchangeable behind the
registry in ``base``:

- ``download()``   -> fetch the real corpus into the gitignored raw dir.
- ``build(path)``  -> parse a raw-format file (real or a fixture) into a Benchmark.
- ``fixture()``    -> load the committed, format-faithful synthetic mini-set.

CI scores ``fixture()`` (offline, deterministic, no license risk). Full runs use
``download()`` then ``build()``.
"""

from harness.datasets.base import (
    LOADERS,
    LoaderSpec,
    get_loader,
    list_benchmarks,
    load_fixture,
)

__all__ = [
    "LOADERS",
    "LoaderSpec",
    "get_loader",
    "list_benchmarks",
    "load_fixture",
]
