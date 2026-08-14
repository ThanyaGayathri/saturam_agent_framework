"""Fail-closed tenant scoping at the memory data layer (SM-C4 #92).

The ticket's Decision 1 is that a forgotten scope must **error**, at the data layer, rather than
be treated as "no predicate". Its Decision 2 is that the rule holds on both substrates. So the
assertions here are deliberately *structural* rather than a list of cases someone thought of:

* the entry guard is asserted by **parsing** every adapter, so an adapter added later without one
  fails CI rather than being caught in review;
* the seam's verb list is compared against ``MemoryProvider``'s abstract methods, so a verb added
  without a guard fails too;
* the Cypher builders are discovered by **signature**, so a statement added later is covered
  without this file changing.

What none of it proves is that a *backend* applies the predicate it was handed -- that is a
property of the statement it issues (``test_neo4j_graph_statements``) and, for a real store,
SM-X6's (#142) adversarial suite. Isolation across two live tenants on a real backend is not
provable here, and this file does not claim it.
"""

import ast
import asyncio
import inspect
import pathlib
from dataclasses import dataclass, fields
from types import SimpleNamespace
from typing import Any, Callable, Dict, List

import pytest

from egpt.integrations.memory.graph.neo4j import cypher
from egpt.memory.config import build_memory_provider
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    SCOPE_SLOTS,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemoryScopeRequiredError,
    Scope,
)
from egpt.memory.validation import MEMORY_DATA_ACCESS_METHODS, assert_scope_isolation, require_scope
from tests.unit.memory.test_provider_swap import ALL_CONFIGS

ACME = Scope(tenant_id="acme", user_id="u1")


def build(config_name: str) -> MemoryProvider:
    """A provider from the swap suite's wiring matrix -- the same four this seam ships."""
    return build_memory_provider(ALL_CONFIGS[config_name]())


#: Every adapter class the registry can produce, by name. Derived from the wirings rather than
#: hand-listed, so an adapter registered later is checked without this file changing.
ADAPTER_CLASSES = {type(build(name)).__name__: type(build(name)) for name in ALL_CONFIGS}


# ---------------------------------------------------------------------------
# The shapes a caller can arrive with instead of a Scope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnvalidatedScope(Scope):
    """A subclass whose ``__post_init__`` never reaches ``Scope``'s tenant check.

    Not a contrived shape: overriding ``__post_init__`` is the ordinary way to extend a dataclass,
    and it is the one route by which a real ``Scope`` instance can carry a blank tenant. It is why
    :func:`require_scope` re-asserts the tenant instead of stopping at ``isinstance``.
    """

    def __post_init__(self) -> None:  # deliberately does not call super()
        pass


#: Every unscoped shape, and what each one would do if it reached a backend.
UNSCOPED = {
    # No scope at all -- the plain forgotten argument.
    "none": None,
    # Looks like a Scope, is not one: reaches a predicate untrimmed, so `"acme "` writes rows into
    # a tenant no trimmed caller can name again.
    "duck_typed": SimpleNamespace(tenant_id="acme ", agent_id=None, user_id="u1", team_id=None, session_id=None),
    # A real Scope carrying a blank tenant, via a subclass that skips the construction-time check.
    "blank_tenant": UnvalidatedScope(tenant_id="", user_id="u1"),
    # Whitespace is the same case one step subtler: `.strip()` is what makes it unscoped.
    "whitespace_tenant": UnvalidatedScope(tenant_id="   ", user_id="u1"),
}


# ---------------------------------------------------------------------------
# Calling every data-access method with an unscoped argument
# ---------------------------------------------------------------------------


def call_with(provider: MemoryProvider, method: str, scope: Any) -> Any:
    """Invoke one data-access verb with ``scope``, with otherwise-valid arguments.

    One dispatcher so the matrix below covers every verb by name: a verb added to
    ``MEMORY_DATA_ACCESS_METHODS`` and not here raises ``KeyError`` rather than being skipped.
    """
    query = MemoryQuery(text="anything", tiers=[MemoryTier.SEMANTIC])
    events = [MemoryEvent(content="a fact worth keeping", scope=ACME)]
    fn = getattr(provider, method)
    args: Dict[str, Callable[[], Any]] = {
        "read": lambda: fn(scope, query),
        "aread": lambda: fn(scope, query),
        "read_tier": lambda: fn(scope, MemoryTier.SEMANTIC, query),
        "aread_tier": lambda: fn(scope, MemoryTier.SEMANTIC, query),
        "write": lambda: fn(scope, events),
        "awrite": lambda: fn(scope, events),
        "write_tier": lambda: fn(scope, MemoryTier.SEMANTIC, events),
        "awrite_tier": lambda: fn(scope, MemoryTier.SEMANTIC, events),
        "consolidate": lambda: fn(scope),
        "aconsolidate": lambda: fn(scope),
    }
    return args[method]()


def durable_state(provider: MemoryProvider) -> str:
    """Everything the provider's backing store holds, as one comparable string.

    A refusal that has already written is not fail-closed, and a receipt-shaped assertion cannot
    see the difference -- the refusal raises, so there is no receipt to inspect.
    """
    parts: List[str] = []
    vector = getattr(provider, "vector", None)
    if vector is not None:
        parts.append(repr(sorted((tenant, sorted(bucket)) for tenant, bucket in vector.rows.items())))
    graph = getattr(provider, "graph", None)
    if graph is not None:
        parts.append(repr(sorted((tenant, sorted(r.id for r in rows)) for tenant, rows in graph.rows.items())))
        parts.append(repr(len(graph.facts)))
    manager = getattr(provider, "manager", None)
    if manager is not None:
        parts.append(repr([m.memory for m in manager.memories]))
    assert parts, "no backing store found on this provider; the snapshot would be vacuously equal"
    return "|".join(parts)


def run(result: Any) -> Any:
    """Await a coroutine, pass anything else through -- so one matrix covers both halves."""
    if inspect.iscoroutine(result):
        return asyncio.run(result)
    return result


@pytest.mark.parametrize("config_name", sorted(ALL_CONFIGS))
@pytest.mark.parametrize("method", MEMORY_DATA_ACCESS_METHODS)
@pytest.mark.parametrize("shape", sorted(UNSCOPED))
def test_every_data_access_method_refuses_an_unscoped_call(config_name, method, shape):
    """The acceptance criterion, over every verb, every wiring, and both halves.

    Parametrised over ``ALL_CONFIGS`` rather than the fully-wired one: four of one review round's
    twelve majors were an invariant that held on the wiring the test used and not its neighbour.
    """
    provider = build(config_name)

    with pytest.raises(MemoryScopeRequiredError) as excinfo:
        run(call_with(provider, method, UNSCOPED[shape]))

    # The refusal names the verb the caller used, not the one it delegated to -- `read_tier` must
    # not report `read`. That is the whole reason each entry point carries its own guard.
    assert f"{method}()" in str(excinfo.value)


@pytest.mark.parametrize("config_name", sorted(ALL_CONFIGS))
@pytest.mark.parametrize("method", [m for m in MEMORY_DATA_ACCESS_METHODS if "write" in m or "consolidate" in m])
@pytest.mark.parametrize("shape", sorted(UNSCOPED))
def test_a_refused_call_writes_nothing(config_name, method, shape):
    """Fail-closed means refused *before* the backend, not refused after the row landed."""
    provider = build(config_name)
    before = durable_state(provider)

    with pytest.raises(MemoryScopeRequiredError):
        run(call_with(provider, method, UNSCOPED[shape]))

    assert durable_state(provider) == before


@pytest.mark.parametrize("config_name", sorted(ALL_CONFIGS))
def test_a_scoped_call_still_works(config_name):
    """The guard refuses the unscoped call and nothing else.

    Without this the matrix above is satisfied by a provider that refuses everything.
    """
    provider = build(config_name)
    slice_ = provider.read(ACME, MemoryQuery(text="anything", tiers=[MemoryTier.SEMANTIC]))
    assert slice_.scope == ACME


# ---------------------------------------------------------------------------
# The guard cannot be dropped from an adapter added later
# ---------------------------------------------------------------------------


def _guarded_methods(cls: type) -> Dict[str, str]:
    """For each data-access method on ``cls``, the ``method=`` literal its first statement guards
    with -- or the reason it does not qualify."""
    source = pathlib.Path(inspect.getfile(cls)).read_text()
    tree = ast.parse(source)
    class_node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls.__name__)

    found: Dict[str, str] = {}
    for node in class_node.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in MEMORY_DATA_ACCESS_METHODS:
            continue
        body = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        first = body[0] if body else None
        call = first.value if isinstance(first, ast.Expr) and isinstance(first.value, ast.Call) else None
        if call is None or getattr(call.func, "id", None) != "require_scope":
            found[node.name] = f"first statement is not a require_scope call: {ast.dump(first) if first else 'empty'}"
            continue
        literal = next((kw.value.value for kw in call.keywords if kw.arg == "method"), None)
        found[node.name] = str(literal)
    return found


@pytest.mark.parametrize("class_name", sorted(ADAPTER_CLASSES))
def test_every_adapter_guards_every_data_access_method_first(class_name):
    """Parsed rather than called, because a method with no test is exactly the one that loses its
    guard. The guard must be **first**: a check after the fetch has already read the rows."""
    cls = ADAPTER_CLASSES[class_name]
    guarded = _guarded_methods(cls)

    assert sorted(guarded) == sorted(MEMORY_DATA_ACCESS_METHODS), (
        f"{class_name} does not define every data-access method, or defines one this test cannot see"
    )
    for method, literal in guarded.items():
        assert literal == method, f"{class_name}.{method} guards with method={literal!r}"


def test_the_data_access_list_covers_every_verb_on_the_seam():
    """A verb added to ``MemoryProvider`` is either data access or introspection, and the first
    kind needs a guard. Without this the list above quietly stops being complete."""
    abstract = set(MemoryProvider.__abstractmethods__)
    introspection = {"substrates", "tiers"}
    assert abstract - introspection == set(MEMORY_DATA_ACCESS_METHODS)


# ---------------------------------------------------------------------------
# One declaration of the slots, not five
# ---------------------------------------------------------------------------


def test_the_slot_tuple_is_exactly_the_scope_dataclass():
    """A slot added to ``Scope`` and not to ``SCOPE_SLOTS`` reaches no predicate at all."""
    assert SCOPE_SLOTS == tuple(f.name for f in fields(Scope))


def test_the_graph_backend_filters_on_the_same_slots_the_seam_declares():
    """Identity, not equality: two tuples that happen to match today are two declarations."""
    assert cypher.SCOPE_SLOTS is SCOPE_SLOTS


def test_the_scope_key_covers_every_slot():
    """The write identity and the read predicate must partition on the same set. A slot missing
    from the key collides two scopes that a predicate would separate."""
    for slot in SCOPE_SLOTS:
        if slot == "tenant_id":
            narrow, wide = Scope(tenant_id="acme"), Scope(tenant_id="globex")
        else:
            narrow, wide = Scope(tenant_id="acme", **{slot: "x"}), Scope(tenant_id="acme")
        assert narrow.key() != wide.key(), f"{slot} does not affect Scope.key()"


# ---------------------------------------------------------------------------
# The graph substrate's own layer
# ---------------------------------------------------------------------------


def scope_taking_builders() -> List[Callable[..., Any]]:
    """Every public builder in ``cypher`` that takes a scope, found by signature.

    Discovered rather than listed: a statement added later is covered by this property without
    anyone remembering to extend a tuple, which is the failure mode SM-R2 section 5.4 counted
    three of.
    """
    builders = []
    for name in cypher.__all__:
        obj = getattr(cypher, name)
        if callable(obj) and "scope" in inspect.signature(obj).parameters:
            builders.append(obj)
    assert len(builders) >= 8, "the discovery lost its subjects; a rename would make this vacuous"
    return builders


@pytest.mark.parametrize("shape", sorted(UNSCOPED))
def test_no_cypher_can_be_built_without_a_tenant(shape):
    """The data-layer half of Decision 1: ``provider.graph`` is public, so the seam's guard is not
    the last line. Every builder refuses, including the two that bind no scope parameter."""
    for builder in scope_taking_builders():
        args = ["e"] if "alias" in inspect.signature(builder).parameters else []
        with pytest.raises(MemoryScopeRequiredError):
            builder(*args, UNSCOPED[shape]) if args else builder(UNSCOPED[shape])


def test_a_scoped_builder_still_emits_the_tenant_predicate():
    """The counterweight: the guard must not be satisfied by refusing everything."""
    predicate, params = cypher.scope_predicate("e", ACME)
    assert "e.tenant_id = $tenant_id" in predicate
    assert params["tenant_id"] == "acme"


# ---------------------------------------------------------------------------
# The post-condition, per slot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slot", SCOPE_SLOTS)
def test_a_record_leaked_on_any_slot_is_refused(slot):
    """Not just the tenant. A backend that honours the tenant and drops ``user_id`` serves one
    user's memory to another, which is the same leak one boundary in -- and a tenant-only guard
    watches it happen."""
    call_scope = Scope(tenant_id="acme", agent_id="a1", user_id="u1", team_id="t1", session_id="s1")
    leaked = Scope(**{**{f.name: getattr(call_scope, f.name) for f in fields(Scope)}, slot: "other"})
    record = MemoryRecord(id="r1", tier=MemoryTier.SEMANTIC, content="a leaked row", scope=leaked)

    with pytest.raises(MemoryOperationError) as excinfo:
        assert_scope_isolation(call_scope, [record], method="read")

    assert slot in str(excinfo.value)
    # The offending values never appear: this fires exactly when another tenant's rows are in
    # hand, so echoing their identifiers puts them in a log line.
    assert "other" not in str(excinfo.value)


@pytest.mark.parametrize("shape", sorted(UNSCOPED))
def test_the_isolation_guard_refuses_to_run_unscoped(shape):
    """Every slot unset constrains nothing, so an unscoped guard passes every record. Vacuous is
    worse than wrong here: nothing about the call looks like a failure."""
    leaked = MemoryRecord(id="r1", tier=MemoryTier.SEMANTIC, content="another tenant's row", scope=ACME)
    with pytest.raises(MemoryScopeRequiredError):
        assert_scope_isolation(UNSCOPED[shape], [leaked], method="read")


def test_an_unset_slot_constrains_nothing():
    """``None`` means "any". Treating it as a predicate would refuse a legitimately wide read."""
    wide = Scope(tenant_id="acme")
    record = MemoryRecord(
        id="r1", tier=MemoryTier.SEMANTIC, content="in tenant, narrower", scope=Scope(tenant_id="acme", user_id="u1")
    )
    assert_scope_isolation(wide, [record], method="read")


def test_require_scope_returns_the_scope_it_validated():
    """So a caller can write ``scope = require_scope(scope, ...)`` without a second lookup."""
    assert require_scope(ACME, method="read") is ACME
