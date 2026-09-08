# Executor registry

*P0 deliverable. Authored jointly by Lane K and Lane R. A convention, not a contract.*

The named deterministic steps a workflow may compose. The delivery plan is explicit that
trying to *finish* this in P0 is the mistake — the list grows through P4. What P0 fixes is
the signature shape, the naming rule, and the check that stops the one failure mode this
mechanism has.

---

## Why it needs a rule at all

Executors are resolved **by the Python function's `__name__`**, and the namespace is flat.

`Step.to_dict` (`workflow/step.py:298`) stores:

```python
result["executor_ref"] = self.executor.__name__
```

`Step.from_dict` (`workflow/step.py:413`) resolves it:

```python
executor = registry.get_function(config["executor_ref"])
```

And `Registry.get_function` (`registry/registry.py:366`) is:

```python
return next((f for f in self.functions if f.__name__ == name), None)
```

First match by name, over a plain list. Two consequences, both verified:

**A duplicate name resolves silently to whichever was registered first.** Two functions
called `check_freshness`, one authored in each lane, produce no warning at registration and
no warning at resolution — the workflow simply runs the other lane's step. This is worth
contrasting with the *tool* lookup a few lines up in the same file, which explicitly warns
that "multiple distinct tools share the name". `get_function` has no such guard.

**Renaming a Python function is a breaking change.** Every stored workflow holds the old
name as a string. Renaming it means `get_function` returns `None`, and `Step` construction
then fails with "must have one executor" — loudly, which is the good case, but it fails for
every stored workflow at once.

So the namespace is global, silent on collision, and cemented into stored data. That is
exactly the kind of thing two people building in parallel get wrong once and then debug for
a day.

---

## The signature shape

```python
def <verb>_<object>(step_input: StepInput) -> StepOutput:
    ...
```

`StepExecutor` (`workflow/step.py:88`) also permits async and streaming forms, and the engine
detects them at runtime (`inspect.iscoroutinefunction`, `inspect.isasyncgenfunction`).

**One executor, one name.** Where an executor does I/O, author it as `async def` under the
same name. Do **not** author a sync and an async twin.

This is a deliberate departure from the repository's standing sync/async rule, and the reason
is the flat namespace above. That rule exists for public API where a *caller* picks a variant.
An executor has exactly one caller — the workflow engine — which handles both forms already,
so publishing `check_freshness` and `acheck_freshness` would double a namespace that cannot
survive ambiguity, to give a choice nobody makes. Contracts and providers still follow the
standing rule; this exemption is executors only.

---

## The naming rule

- **`<verb>_<object>`, snake_case.** `check_freshness`, `record_provenance`, `evaluate_alert`.
- **Globally unique across both lanes.** Not prefixed by lane: a lane prefix would be baked
  into stored workflows forever and would have to survive someone changing lanes. Uniqueness
  is enforced by the check below instead.
- **The name is the API.** Renaming one is a breaking change under the change protocol: both
  lanes agree first, and someone migrates the stored workflows.
- **Verbs are specific.** `process_data` and `handle_step` are names two people will both
  reach for. Whatever it actually does, say that.

## What may be an executor

Anything deterministic, and — from the alignment rules — **everything that permits or blocks**.
Preflight checks, the SQL guard, feasibility, gap checks, conformance checks, alert
evaluation, delivery, the provenance record. The model explains and proposes; it is never the
thing that says yes.

An executor must not call a model. If it needs judgment, it is an agent step, and the
judgment belongs in a skill under the K14 template.

---

## Registering

Both lanes append to `Registry.functions`. Before the registry is handed to `EgptOS`, run:

```python
from egpt_retail.executors.registry import assert_registry_valid

assert_registry_valid(registry.functions)
```

It refuses duplicate names and non-conforming signatures, with the offending names in the
message. Wire it into the application factory next to K1's memory startup check, for the same
reason: both are misconfigurations that otherwise surface as a wrong answer rather than as a
failure.

---

## The list

Grows through P4. Each entry: name, owner, one line, and what it refuses.

| Name | Owner | Does | Refuses |
|---|---|---|---|
| _(none yet — P0 fixes the shape, not the list)_ | | | |

Lane R's R7 executor library is the first substantial batch: preflight checks, dry run,
delivery, provenance. Lane K's first are feasibility (K11) and business-area detection (K9),
both already deterministic by design.

Add a row when you add an executor. A registry nobody updates is one people stop reading, and
then two lanes name the same step twice — which is the failure this whole document exists to
prevent.

---

## Verification

`cookbook/94_retail/K00_executor_registry.py` demonstrates the collision against the real
`Registry`, then shows the check catching it.
