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

from egpt.memory.config import MemoryProviderConfig, build_memory_provider
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
    require_scope,
)
from egpt.memory.validation import MEMORY_DATA_ACCESS_METHODS, assert_scope_isolation
from egpt.providers.model_provider import ProviderMode
from tests.unit.memory._fakes import FakeGraph, FakeVector
from tests.unit.memory.test_provider_swap import ALL_CONFIGS, WRITABLE_CONFIGS

ACME = Scope(tenant_id="acme", user_id="u1")


def cypher_module() -> Any:
    """The Cypher builders, or a skip when the driver is absent.

    Gated **per test** rather than at module scope, which is what
    ``test_neo4j_graph_statements`` does: everything else in this file is the seam's own
    fail-closed contract and needs no driver, and skipping all of it on a partial install would
    take the core acceptance criterion offline in exactly the job built to notice that.

    The statements themselves import nothing -- they are string builders over
    ``egpt.memory.types`` -- but the package ``__init__`` eagerly imports the substrate, which
    imports the driver. So the gate is on the package, not on what these tests use.
    """
    # Gated on ``neo4j`` itself, not on the module being imported: the substrate raises a
    # hand-built ``ImportError`` carrying no ``name``, so ``importorskip`` on the cypher module
    # cannot match it to a missing dependency and re-raises instead of skipping.
    pytest.importorskip("neo4j", reason="the graph package __init__ imports the Neo4j driver")
    from egpt.integrations.memory.graph.neo4j import cypher

    return cypher


#: How many public builders in ``cypher`` take a scope. Exact rather than a floor: the point of
#: the discovery below is that a builder added later is covered, and a floor would equally pass
#: when one is removed or renamed out of the sweep.
SCOPE_TAKING_BUILDERS = 15


def build(config_name: str) -> MemoryProvider:
    """A provider from the swap suite's wiring matrix -- the same four this seam ships."""
    return build_memory_provider(ALL_CONFIGS[config_name]())


#: Every adapter class the registry can produce, by name. Derived from the wirings rather than
#: hand-listed, so an adapter registered later is checked without this file changing.
ADAPTER_CLASSES = {type(provider).__name__: type(provider) for provider in (build(name) for name in ALL_CONFIGS)}


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
    # Padded but not blank, and the only shape here that survives the early tenant check --
    # `"acme ".strip()` is truthy -- so it is refused by the slot loop instead, under a different
    # message. Without it the whole matrix below exercises one of the two refusal paths.
    "padded_tenant": UnvalidatedScope(tenant_id="acme ", user_id="u1"),
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
        # Identities, not `len`: a count-preserving swap -- one fact replaced by another -- would
        # compare equal and let a guard that had already written pass as if it had not.
        parts.append(repr(sorted(f.record.id for f in graph.facts)))
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


@pytest.mark.parametrize("config_name", sorted(WRITABLE_CONFIGS))
def test_a_scoped_call_still_works(config_name):
    """The guard refuses the unscoped call and nothing else.

    Without this the matrix above is satisfied by a provider that refuses everything -- and read
    alone would not have shown it: a guard that broke a scoped ``write_tier`` or ``consolidate``
    passes a read-only counterweight. So the write and consolidate lanes are exercised too, on
    every wiring that serves the tier the seam's shared workload uses.
    """
    provider = build(config_name)
    events = [MemoryEvent(content="the customer prefers email", scope=ACME)]

    receipt = provider.write_tier(ACME, MemoryTier.SEMANTIC, events)
    report = provider.consolidate(ACME)
    slice_ = provider.read(ACME, MemoryQuery(text="the customer", tiers=[MemoryTier.SEMANTIC]))

    assert receipt.accepted == 1
    assert report.scope == ACME
    assert slice_.scope == ACME


@pytest.mark.parametrize("config_name", sorted(ALL_CONFIGS))
def test_a_scoped_read_still_works_on_every_wiring(config_name):
    """The read half of the counterweight, including ``graph_only`` -- which serves no semantic
    write, so it cannot join the lane above but must still answer a scoped read."""
    provider = build(config_name)
    slice_ = provider.read(ACME, MemoryQuery(text="anything", tiers=[MemoryTier.SEMANTIC]))
    assert slice_.scope == ACME


# ---------------------------------------------------------------------------
# The guard cannot be dropped from an adapter added later
# ---------------------------------------------------------------------------


def _first_require_scope_call(statement: Any) -> Any:
    """The ``require_scope`` call a statement makes, whether or not it binds the result.

    Both forms are legitimate guards -- `require_scope(scope, ...)` and
    `scope = require_scope(scope, ...)`, which is the narrowing form the return value exists for.
    Recognising only the bare expression would make the docstring's advertised usage fail CI with
    "first statement is not a require_scope call", which reads as a missing guard.
    """
    if isinstance(statement, ast.Expr):
        call = statement.value
    elif isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is not None:
        call = statement.value
    else:
        return None
    if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "require_scope":
        return call
    return None


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
        call = _first_require_scope_call(first)
        if call is None:
            found[node.name] = f"first statement is not a require_scope call: {ast.dump(first) if first else 'empty'}"
            continue
        literal = next((kw.value.value for kw in call.keywords if kw.arg == "method"), None)
        found[node.name] = str(literal)
    return found


@pytest.mark.parametrize(
    "form",
    [
        "    require_scope(scope, method='read')\n    return 1",
        "    scope = require_scope(scope, method='read')\n    return 1",
        "    scope: Scope = require_scope(scope, method='read')\n    return 1",
    ],
    ids=["bare", "assigned", "annotated"],
)
def test_both_guard_forms_are_recognised(form):
    """The docstring offers the return value; CI must not reject the only way to use it."""
    node = ast.parse(f"def read(self, scope):\n{form}").body[0]
    body = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    assert _first_require_scope_call(body[0]) is not None


def test_a_first_statement_that_is_not_the_guard_is_still_rejected():
    """The counterweight: widening the accepted forms must not accept an unguarded method."""
    node = ast.parse("def read(self, scope):\n    scope = normalise(scope)\n    return 1").body[0]
    assert _first_require_scope_call(node.body[0]) is None


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
    # The abstract methods that are not data access. `close`/`aclose` are absent because they are
    # concrete no-ops on the ABC, not because they were judged non-data-access -- if either
    # becomes abstract it lands in `abstract` and fails here, which is the review this set wants.
    non_data_access_abstracts = {"substrates", "tiers"}
    assert abstract - non_data_access_abstracts == set(MEMORY_DATA_ACCESS_METHODS)


# ---------------------------------------------------------------------------
# One declaration of the slots, not five
# ---------------------------------------------------------------------------


def test_the_slot_tuple_is_exactly_the_scope_dataclass():
    """A slot added to ``Scope`` and not to ``SCOPE_SLOTS`` reaches no predicate at all."""
    assert SCOPE_SLOTS == tuple(f.name for f in fields(Scope))


def test_the_graph_backend_filters_on_the_same_slots_the_seam_declares():
    """Identity, not equality: two tuples that happen to match today are two declarations."""
    assert cypher_module().SCOPE_SLOTS is SCOPE_SLOTS


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
    cypher = cypher_module()
    builders = []
    for name in cypher.__all__:
        obj = getattr(cypher, name)
        if callable(obj) and "scope" in inspect.signature(obj).parameters:
            builders.append(obj)
    assert len(builders) == SCOPE_TAKING_BUILDERS, (
        f"expected {SCOPE_TAKING_BUILDERS} scope-taking builders, found {len(builders)}. A floor "
        "would let a rename or a deletion pass silently, which is the failure this count guards."
    )
    return builders


@pytest.mark.parametrize("shape", sorted(UNSCOPED))
def test_no_cypher_can_be_built_without_a_tenant(shape):
    """The data-layer half of Decision 1: ``provider.graph`` is public, so the seam's guard is not
    the last line. Every builder refuses, including the two that bind no scope parameter.

    And each refusal **names the builder the caller invoked**, not the ``scope_predicate`` it
    routes through. Six of the ten are guarded transitively, so without the verb threaded through
    they all reported one name and sent a reader to a helper they never called -- the same defect
    as a post-condition reporting the verb it delegated to, one layer down.
    """
    for builder in scope_taking_builders():
        args = ["e"] if "alias" in inspect.signature(builder).parameters else []
        with pytest.raises(MemoryScopeRequiredError) as excinfo:
            builder(*args, UNSCOPED[shape]) if args else builder(UNSCOPED[shape])
        assert str(excinfo.value).startswith(f"{builder.__name__}()")


class LeakyVector(FakeVector):
    """A backend that drops the scope predicate -- the defect the post-condition exists to catch.

    Overrides the guarded methods entirely, so the record it returns is out of scope by
    construction rather than by a scope this test had to smuggle past a guard.
    """

    def search(self, scope, query, tier=None):  # type: ignore[no-untyped-def]
        require_scope(scope, method="search")
        return [
            MemoryRecord(
                id="leak",
                tier=MemoryTier.SEMANTIC,
                content="another tenant's row",
                scope=Scope(tenant_id="globex", user_id="u1"),
            )
        ]

    async def asearch(self, scope, query, tier=None):  # type: ignore[no-untyped-def]
        # Its own guard, not the one inside the `search` it delegates to. Without it this half
        # refuses under the sync verb -- the exact misdirection the guards are named for, and it
        # was here until parametrising the double sweep over an *inheriting* double surfaced it.
        require_scope(scope, method="asearch")
        await asyncio.sleep(0)
        return self.search(scope, query, tier)


def scope_taking_methods(obj: Any) -> List[str]:
    """Every public method on a substrate that takes a scope, found by signature.

    ``getmembers`` rather than ``vars``: the latter returns only what the concrete class defines,
    so a double that grew a mixin or a base class would have its inherited verbs skipped in
    silence -- which is the opposite of what "found by signature" promises.
    """
    return sorted(
        name
        for name, fn in inspect.getmembers(type(obj), predicate=callable)
        if not name.startswith("_") and "scope" in inspect.signature(fn).parameters
    )


@pytest.mark.parametrize("double", [FakeVector, FakeGraph, LeakyVector], ids=lambda d: d.__name__)
@pytest.mark.parametrize("shape", sorted(UNSCOPED))
def test_no_substrate_double_answers_an_unscoped_call(double, shape):
    """A double must be **stricter than the loosest real backend**, and the one real substrate in
    the tree refuses at its statement layer. Reached directly, because ``provider.vector`` and
    ``provider.graph`` are public attributes -- which is the whole reason the seam's guard is not
    the last line.

    Discovered by signature, so a verb added to a double is covered without editing this test.
    """
    substrate = double()
    query = MemoryQuery(text="anything", tiers=[MemoryTier.SEMANTIC])
    arguments: Dict[str, tuple] = {
        "upsert": ([],),
        "search": (query,),
        "scan": (MemoryTier.SEMANTIC,),
        "upsert_episodes": ([],),
        "upsert_facts": ([],),
        "upsert_entities": ([],),
        "traverse": (query,),
        # Filtered, because an unfiltered recall is refused for being an enumeration -- and a
        # refusal for the wrong reason would let an unscoped call pass this test on the wrong error.
        "recall_episodes": (["berlin"],),
        "recall_facts": (["berlin"],),
        "bounds_for_ids": ([],),
        "invalidate": ("edge-1", 100),
    }
    methods = scope_taking_methods(substrate)
    assert methods, "the discovery lost its subjects"
    for name in methods:
        rest = arguments[name[1:] if name.startswith("a") and name[1:] in arguments else name]
        with pytest.raises(MemoryScopeRequiredError) as excinfo:
            run(getattr(substrate, name)(UNSCOPED[shape], *rest))
        # Each verb names itself, as the adapters do: a refusal that reports the method it
        # delegated to sends a reader to the wrong frame.
        assert f"{name}()" in str(excinfo.value)


def test_the_double_discovery_sees_inherited_methods():
    """``LeakyVector`` defines two verbs and inherits four.

    A discovery reading only the concrete class would check a third of its surface and still
    report success -- so the sweep above would silently stop covering an inheriting double, which
    is the shape a real backend subclassing a base adapter would take.
    """
    assert scope_taking_methods(LeakyVector()) == scope_taking_methods(FakeVector())


def test_a_scoped_builder_still_emits_the_tenant_predicate():
    """The counterweight: the guard must not be satisfied by refusing everything."""
    predicate, params = cypher_module().scope_predicate("e", ACME)
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


@pytest.mark.parametrize("method", ["read", "aread", "read_tier", "aread_tier"])
def test_a_leak_names_the_verb_the_caller_used(method):
    """The post-condition must agree with the entry guard about which method is running.

    ``read_tier`` delegates to the shared read path and ``aread`` is a different code path
    entirely, so a message naming ``read`` for either one sends a reader to the wrong frame --
    the async half especially, where the two implementations genuinely differ.
    """
    provider = build_memory_provider(
        MemoryProviderConfig(
            provider="co_located", mode=ProviderMode.LOCAL, options={"vector_substrate": LeakyVector()}
        )
    )
    query = MemoryQuery(text="row", tiers=[MemoryTier.SEMANTIC])
    arguments = (ACME, MemoryTier.SEMANTIC, query) if "tier" in method else (ACME, query)

    with pytest.raises(MemoryOperationError) as excinfo:
        run(getattr(provider, method)(*arguments))

    assert str(excinfo.value).startswith(f"{method}()")


def test_an_unset_slot_constrains_nothing():
    """``None`` means "any". Treating it as a predicate would refuse a legitimately wide read."""
    wide = Scope(tenant_id="acme")
    record = MemoryRecord(
        id="r1", tier=MemoryTier.SEMANTIC, content="in tenant, narrower", scope=Scope(tenant_id="acme", user_id="u1")
    )
    assert_scope_isolation(wide, [record], method="read")


def test_require_scope_returns_the_scope_it_validated():
    """So a caller holding a loosely-typed value gets the narrowing the return type promises."""
    assert require_scope(ACME, method="read") is ACME


@pytest.mark.parametrize("shape", sorted(UNSCOPED))
@pytest.mark.parametrize("method", MEMORY_DATA_ACCESS_METHODS)
def test_the_refusal_carries_the_verb_structurally(method, shape):
    """``verb`` is an attribute, not a substring of the message.

    The error *type* answers "was anything called unscoped". Paging specifically on an unscoped
    ``consolidate`` needed a regex over prose otherwise, and the name is already in hand at every
    raise -- so the information was there and only the structure was missing.
    """
    with pytest.raises(MemoryScopeRequiredError) as excinfo:
        require_scope(UNSCOPED[shape], method=method)

    assert excinfo.value.verb == method


def test_a_blank_tenant_is_refused_as_a_tenant_failure_not_a_generic_slot_one():
    """The tenant has its own check ahead of the slot loop, and its own ``field_name``.

    Without this the dedicated check is redundant with the loop -- which would still refuse the
    call, but report it as an ordinary slot problem. An operator alerting on unscoped access
    filters on ``field_name``, and "some slot was malformed" is not that signal.
    """
    with pytest.raises(MemoryScopeRequiredError) as excinfo:
        require_scope(UnvalidatedScope(tenant_id="", user_id="u1"), method="read")

    assert excinfo.value.field_name == "tenant_id"
    assert "unscoped is the widest tenancy" in str(excinfo.value)


@pytest.mark.parametrize("slot", [s for s in SCOPE_SLOTS if s != "tenant_id"])
@pytest.mark.parametrize("value", ["  u1  ", "u1 ", ""], ids=["padded", "trailing", "blank"])
def test_a_non_tenant_slot_that_skipped_normalisation_is_refused(slot, value):
    """``Scope.__post_init__`` normalises all five slots; a subclass that skips it must not slip
    an unnormalised one past the guard.

    ``user_id="  u1  "`` is not a tenancy breach -- it fails closed, matching no row. It is a
    partition split on incidental whitespace: the same user reads and writes two disjoint sets
    depending on how the identifier reached the call, with nothing in the logs.
    """
    scope = UnvalidatedScope(**{"tenant_id": "acme", slot: value})

    with pytest.raises(MemoryScopeRequiredError) as excinfo:
        require_scope(scope, method="read")

    assert excinfo.value.field_name == slot
    assert "read()" in str(excinfo.value)


def test_a_properly_constructed_scope_passes_the_slot_check():
    """The counterweight: ``Scope`` normalises on construction, so every real scope survives --
    including one built from padded input, which is trimmed rather than refused."""
    scope = Scope(tenant_id=" acme ", user_id="  u1  ", agent_id="a1", team_id="t1", session_id="s1")
    assert require_scope(scope, method="read") is scope
    assert scope.user_id == "u1"
