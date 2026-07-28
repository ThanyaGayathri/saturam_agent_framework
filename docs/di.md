# Dependency Injection — contributor guide

This is the working guide for the DI container. For why it exists and why it is hand-written, see
[ADR 0002](adr/0002-dependency-injection.md).

The one-line rule:

> **Users inject objects into agents. The framework injects services into itself.**
> The container is for the second kind of wiring only.

---

## The boundary

| | Public API (constructor injection) | Container (internal wiring) |
|---|---|---|
| Who wires it | The user, in their own code | The framework, at its composition root |
| Examples | `Agent(model=..., tools=..., db=...)`, `Team(members=...)`, `Workflow(steps=...)` | `Registry`, `ServiceAccountVerifier`, `JWTValidator`, `ScheduleExecutor`, `SchedulePoller` |
| Where | `egpt/agent/`, `egpt/team/`, `egpt/workflow/` | `egpt/os/container.py` |
| Rule | Never require a container | Never leak into the user-facing signature |

Things that belong **in** the container:

- Control-plane services (schedulers, verifiers, validators, pollers).
- Integration implementations the framework picks for itself (stores, exporters, clients).
- Collaborators whose implementation a deployment or a test may want to swap.

Things that stay **out**:

- Anything a user passes to `Agent` / `Team` / `Workflow`. `Agent(model=FakeModel())` must keep working with no
  container in sight — there is a test asserting exactly that
  (`libs/agno/tests/unit/os/test_os_container.py::test_agent_runs_without_a_container`).
- Component lookup **by name or id** (`"openai:gpt-5.5"`, a tool name, a db id). That is `Registry`'s job; the
  container binds `Registry` rather than duplicating it.
- Per-request state. Bindings describe the service graph, not the current user.

---

## Using the container

### Resolving

```python
from egpt.os.container import SCHEDULE_EXECUTOR
from egpt.registry import Registry

registry = agent_os.container.resolve(Registry)        # typed as Registry
executor = agent_os.container.resolve(SCHEDULE_EXECUTOR)  # string token -> Any
```

`resolve()` builds the service and its dependencies, caching singletons. Use `try_resolve()` when an unbound token is a
legitimate configuration (it returns `None`; a failing factory still raises). Async variants are `aresolve()` /
`atry_resolve()`, and they also accept `async def` factories — a sync `resolve()` of an async binding raises rather than
returning a coroutine.

### Adding a binding

Bindings live in `libs/agno/egpt/os/container.py`. Factories always receive the container, so they can resolve their own
dependencies:

```python
def _thing_factory(agent_os: "EgptOS"):
    def factory(c: Container) -> Thing:
        return Thing(db=agent_os.db, registry=c.resolve(Registry))

    return factory


bind_default(Thing, _thing_factory(agent_os))
```

Guidelines:

- **Lazy.** Factories run on first resolve, so `build_os_container` can be called from `EgptOS.__init__` before the
  instance is finished.
- **Singleton by default.** Pass `singleton=False` for objects with their own lifecycle (the schedule poller owns a
  polling task, so a second lifespan must get a fresh one).
- **Never overwrite a caller's binding.** `build_os_container` skips tokens that are already bound — a container passed
  to `EgptOS(container=...)` is a deliberate substitution.
- **Policy stays in the caller.** The `ServiceAccountVerifier` factory builds a verifier; whether an instance should
  exist at all (it needs a db) is decided in `os/app.py`.

### Choosing a token

| Use | When |
|-----|------|
| The type itself (`Registry`) | The defining module is import-safe for `egpt[os]`. Preferred — keeps `resolve()` typed. |
| A string constant (`SCHEDULE_EXECUTOR`) | The service sits behind an optional extra, so importing the type eagerly would pull an optional dependency (`egpt[scheduler]` → croniter/pytz). Annotate the call site to keep type checking. |

Declare string tokens as module constants in `egpt/os/container.py`, never as inline literals.

---

## Substituting an implementation in tests

This is the point of the container: swap an implementation without editing production code or monkeypatching a module.

```python
def test_poller_uses_the_stub_executor(agent_os):
    stub = StubScheduleExecutor()

    with agent_os.container.override(SCHEDULE_EXECUTOR, stub):
        poller = agent_os.container.resolve(SCHEDULE_POLLER)
        assert poller.executor is stub
```

On exit the previous binding is restored **and** any singleton constructed inside the block is evicted, so a service
that captured the fake cannot leak into later assertions. `override_factory()` does the same with a factory, and both
work with `async with`.

To substitute before the OS is even built, pass a pre-bound container:

```python
container = Container(name="test")
container.bind_instance(Registry, FakeRegistry())

agent_os = EgptOS(agents=[agent], db=db, container=container)
```

`Container.child()` gives a scope that falls back to its parent — useful when a test (or, later, a request) needs its own
bindings without mutating the application graph.

---

## Layering

Per [ADR 0001](adr/0001-layered-architecture.md):

- `egpt/container.py` is **core**. It is the primitive only and imports nothing but `egpt.exceptions`. Never add a
  binding to a concrete service here.
- `egpt/os/container.py` is the **composition root** in the api layer, and is the only module allowed to know concrete
  implementations.

Adding a container to another layer means that layer has become a composition root — raise it in review before doing it.

---

## Reference

| Method | Purpose |
|--------|---------|
| `bind(token, factory, singleton=True)` | Bind a token to a factory |
| `bind_instance(token, instance)` | Bind an already-constructed object |
| `resolve(token)` / `try_resolve(token)` | Resolve; `try_` returns `None` for an unbound token |
| `aresolve(token)` / `atry_resolve(token)` | Async variants, supporting async factories |
| `override(token, instance)` / `override_factory(token, factory)` | Temporary substitution (context manager) |
| `child(name=None)` | Nested scope that falls back to this container |
| `has(token)` / `token in container` / `tokens()` | Introspection |
| `unbind(token)` / `reset(token=None)` | Remove a binding / drop cached singletons |

Cyclic dependencies raise `ContainerError` naming the chain, rather than recursing until the stack blows.
