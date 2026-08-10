# TEST_LOG.md - 93 Components Cookbook

Test results for `cookbook/93_components/` examples.

**Test Date:** 2026-02-08  
**Environment:** `.venvs/demo/bin/python`

---

## Structure Validation

### check_cookbook_pattern.py

**Status:** PASS

**Description:** Validates cookbook structure for all Python files under `cookbook/93_components/` recursively.

**Result:** `.venvs/demo/bin/python cookbook/scripts/check_cookbook_pattern.py --base-dir cookbook/93_components --recursive` reported `Checked 14 file(s) ... Violations: 0`. Re-run after adding `validate_config.py` reported `Checked 21 file(s) ... Violations: 2`; both violations are pre-existing, in `workflows/registry_agents_in_workflow.py` and `workflows/save_hitl_condition_loop_router.py`.

---

## Runtime Validation

### auto_populate_registry.py

**Status:** PASS

**Description:** Builds a team (two agents with distinct models, one with a custom tool and a db) and a workflow, hands them to EgptOS without an explicit registry, and prints the auto-populated registry. Runs offline (no model calls).

**Result:** Discovered `OpenAI:gpt-5.4` (collected once though shared by two agents), `OpenAI:gpt-5.4-mini`, the `get_weather` tool, and the `auto-registry-db` database. No registry was passed.

---

### auto_populate_registry_os.py

**Status:** PASS

**Description:** Same setup served as an EgptOS app. Verified `get_app()` builds and the registry is auto-populated; the components are served at `GET /registry?resource_type=...`. Constructed without serving (no blocking run).

**Result:** Registry contained the member agents' models and tool with no registry passed. App built successfully.

---

### validate_config.py

**Status:** PASS

**Description:** Validates a valid agent definition and four invalid variants against the same
validator the components API uses (EGPT-2.2). Runs offline -- no database and no model calls.

**Result:** The valid definition was accepted and stamped `schema_version 1`. The four invalid
variants each reported the expected field-level errors: `model / missing_required_field`,
`instruction / unknown_field` (with the "Did you mean 'instructions'?" suggestion),
`tools[0].name / unresolvable_ref`, and a three-error case reporting
`num_history_runs / invalid_value`, `instruction / unknown_field` and
`model.provider / unresolvable_ref` together.

---

### capability_policy.py

**Status:** PASS

**Description:** Exercises the tenant capability policy (EGPT-2.3) against the same validator the
components API uses, plus the hydration-time re-check. Loads `capability_policy.json` from the
same folder. Runs offline -- no database, no server and no model calls.

**Result:** All six sections behaved as documented. (1) A model outside the tenant's allow-list was
rejected with `model / capability_not_permitted`. (2) `run_sql_query` was rejected with
`tools[0].name / privileged_tool_blocked` for the unscoped caller *despite* that tenant holding
`tools: ["*"]` -- confirming a tools wildcard does not reach the privileged denylist. (3) The
identical config was accepted for `acme`, which carries `granted_privileged: ["sql"]`.
(4) The `locked-down` tenant, permitted no models at all, was rejected with
`Permitted models: <none>`. (5) The revoked-grant case raised `PolicyViolationError` at hydration
on the same bytes that were accepted in step 3 -- the property the write-path check cannot provide
on its own. (6) With `policy=None`, nothing was checked.

**Note:** Run against `.venv/bin/python` in this workspace, which has no `.venvs/demo`. The example
imports the real `SQLTools`, so `run_sql_query` classified unambiguously as `sql` rather than
falling back to the static name table.

---

