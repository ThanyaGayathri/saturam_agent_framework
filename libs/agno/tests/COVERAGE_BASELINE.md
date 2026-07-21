# Unit test coverage baseline (EGPT-0.10)

Established as part of [aicoe-tracker#41](https://github.com/Saturam-Inc/aicoe-tracker/issues/41).

| Field | Value |
|-------|-------|
| Date | 2026-07-21 |
| Python | 3.12 |
| Command | `pytest libs/agno/tests/unit --cov=egpt` |
| Tests (main CI pre-change) | 10,175 passed, 55 skipped |
| Coverage | See CI job summary and `coverage-egpt-py3.12` artifact |

The PR gate runs on every pull request via [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
Coverage percentage is published in the GitHub Actions step summary for the `test-and-build (egpt, py3.12)` job.

## Quarantined tests (explicit skip/xfail)

| Location | Mechanism | Reason |
|----------|-----------|--------|
| `tests/unit/models/litellm/` | `conftest.py` skip + `importorskip` | litellm extra disabled pending PyPI security review |
| `tests/unit/tools/test_crawl4ai.py` | `pytestmark.skip` + `importorskip` | crawl4ai depends on disabled litellm extra |
| `tests/unit/tools/test_scrapegraph.py` | `skipif` Python `<3.12` | scrapegraph-py requires Python 3.12+ |
| `tests/unit/os/test_*` (3 files) | `conftest.py` skip on Python 3.10 | JWT/trace tests require Python 3.11+ |

Integration and system tests run separately via [`.github/workflows/integration.yml`](../../.github/workflows/integration.yml).
