# Unit test coverage baseline (EGPT-0.10)

Established as part of [aicoe-tracker#41](https://github.com/Saturam-Inc/aicoe-tracker/issues/41).

| Field | Value |
|-------|-------|
| Date | 2026-07-21 |
| Python | 3.12 |
| Command | `pytest libs/agno/tests/unit --cov=egpt --cov-report=json:coverage-egpt.json` |
| Tests | 10,109 passed, 57 skipped |
| Coverage | **43.05%** |

Notes:

- Local baseline run excluded `tests/unit/vectordb/test_lancedb.py` (no macOS x86_64 wheel for lancedb) and `tests/unit/tools/test_brave_search.py` (invalid package metadata). Linux CI installs the full `egpt[tests]` extra and is the authoritative gate.
- Coverage percentage is also published in the GitHub Actions step summary for `test-and-build (egpt, py3.12)` and uploaded as the `coverage-egpt-py3.12` artifact.
- Full credentialed integration/system runs require `workflow_dispatch` (or nightly) of [`.github/workflows/integration.yml`](../../.github/workflows/integration.yml) with repository secrets configured.

The PR gate runs on every pull request via [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).

Local equivalent: `./libs/agno/scripts/test.sh` (or `./scripts/test.sh`).

## Quarantined tests (explicit reasons)

Modules that fail at **import/collection** time cannot use `pytest.mark.skip` alone; they use
`pytest_ignore_collect` (or `importorskip`) so the PR gate never imports them. Quarantine
reasons are printed once via pytest's terminal reporter when collection is ignored.

| Location | Mechanism | Reason |
|----------|-----------|--------|
| `tests/unit/models/litellm/` | `conftest.py` `pytest_ignore_collect` | litellm extra disabled pending PyPI security review |
| `tests/unit/tools/test_crawl4ai.py` | `pytestmark.skip` + `importorskip` | crawl4ai depends on disabled litellm extra |
| `tests/unit/tools/test_scrapegraph.py` | `importorskip` + `skipif` `<3.12` | runs on Python 3.12 (installed via `egpt[tests]`); skipped on older Pythons |
| `tests/unit/os/test_traces_remote_ownership.py` | `os/conftest.py` `pytest_ignore_collect` on 3.10 | requires Python 3.11+ (`datetime.UTC`) |
| `tests/unit/os/test_jwt_middleware_helpers.py` | `os/conftest.py` `pytest_ignore_collect` on 3.10 | requires Python 3.11+ |
| `tests/unit/os/test_ws_jwt_config.py` | `os/conftest.py` `pytest_ignore_collect` on 3.10 | requires Python 3.11+ |
| `tests/integration/models/litellm/` | `conftest.py` `pytest_ignore_collect` | litellm extra disabled pending PyPI security review |
| `tests/integration/models/litellm_openai/` | `conftest.py` `pytest_ignore_collect` | litellm extra disabled pending PyPI security review |
| `tests/integration/models/mistral/` | `conftest.py` `pytest_ignore_collect` | mistral extra disabled pending PyPI quarantine |
| LanceDb-dependent integration modules | root `integration/conftest.py` `pytest_ignore_collect` when `lancedb` missing | no lancedb wheel / extra not installed |

Integration tests soft-skip when required API keys or local-service opt-in flags are unset
(see `tests/integration/conftest.py`). System tests run only when `OPENAI_API_KEY` is present
in the workflow secrets.

### Not exercised in the nightly CI run (intentional)

The nightly `integration.yml` provides cloud-provider API keys (and
`GOOGLE_CLOUD_PROJECT` for vertexai) but does **not** start local database
services or set the `EGPT_INTEGRATION_*` opt-in flags, so the following suites
always soft-skip in CI and must be run locally with the relevant service up:
`db/postgres`, `db/async_postgres`, `db/async_mongo`, `db/async_mysql`,
`db/surrealdb`, `db/clickhouse`, and the self-hosted-model suites
(`models/ollama`, `models/lmstudio`, `models/vllm`). Wiring service containers
for these is deferred to a follow-up.
