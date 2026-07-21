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

Local equivalent: `./libs/agno/scripts/test.sh` (or `./scripts/test.sh`).

## Quarantined tests (explicit reasons)

Modules that fail at **import/collection** time cannot use `pytest.mark.skip` alone; they use
`pytest_ignore_collect` (or `importorskip`) so the PR gate never imports them.

| Location | Mechanism | Reason |
|----------|-----------|--------|
| `tests/unit/models/litellm/` | `conftest.py` `pytest_ignore_collect` | litellm extra disabled pending PyPI security review |
| `tests/unit/tools/test_crawl4ai.py` | `importorskip("crawl4ai")` + `pytestmark.skip` | crawl4ai depends on disabled litellm extra |
| `tests/unit/tools/test_scrapegraph.py` | `importorskip("scrapegraph_py")` + `skipif` `<3.12` | scrapegraph-py requires Python 3.12+ |
| `tests/unit/os/test_traces_remote_ownership.py` | `os/conftest.py` `pytest_ignore_collect` on 3.10 | requires Python 3.11+ (`datetime.UTC`) |
| `tests/unit/os/test_jwt_middleware_helpers.py` | `os/conftest.py` `pytest_ignore_collect` on 3.10 | requires Python 3.11+ |
| `tests/unit/os/test_ws_jwt_config.py` | `os/conftest.py` `pytest_ignore_collect` on 3.10 | requires Python 3.11+ |

Integration and system tests run separately via [`.github/workflows/integration.yml`](../../.github/workflows/integration.yml)
(nightly + `workflow_dispatch`, credentialed secrets).
