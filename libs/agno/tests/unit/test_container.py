"""Unit tests for the dependency-injection container (EGPT-1.2)."""

import asyncio

import pytest

from egpt.container import Container, ContainerError


class Store:
    """Stand-in for an integration (a store implementation)."""

    def __init__(self, url: str = "postgres://prod"):
        self.url = url


class FakeStore(Store):
    def __init__(self):
        super().__init__(url="memory://fake")


class Indexer:
    """Stand-in for an internal service that depends on an integration."""

    def __init__(self, store: Store):
        self.store = store


@pytest.fixture
def container() -> Container:
    c = Container(name="test")
    c.bind(Store, lambda _c: Store())
    c.bind(Indexer, lambda c_: Indexer(store=c_.resolve(Store)))
    return c


# -- Resolution ---------------------------------------------------------------------


def test_resolves_service_graph(container):
    """The container wires a whole graph, not just single objects."""
    indexer = container.resolve(Indexer)

    assert isinstance(indexer, Indexer)
    assert isinstance(indexer.store, Store)
    assert indexer.store is container.resolve(Store)


def test_singleton_is_cached(container):
    assert container.resolve(Indexer) is container.resolve(Indexer)


def test_transient_binding_builds_each_time():
    c = Container()
    c.bind(Store, lambda _c: Store(), singleton=False)

    assert c.resolve(Store) is not c.resolve(Store)


def test_bind_instance(container):
    fake = FakeStore()
    container.bind_instance(Store, fake)

    assert container.resolve(Store) is fake
    assert container.resolve(Indexer).store is fake


def test_bind_rejects_non_callable():
    c = Container()

    with pytest.raises(ContainerError, match="callable factory"):
        c.bind(Store, Store())  # type: ignore[arg-type]


def test_unknown_token_raises(container):
    with pytest.raises(ContainerError, match="No binding registered"):
        container.resolve(FakeStore)


def test_try_resolve_returns_none_for_unknown_token(container):
    assert container.try_resolve(FakeStore) is None
    assert container.try_resolve(Store) is not None


def test_try_resolve_propagates_factory_errors():
    """An unbound token is a configuration choice; a failing factory is a bug."""
    c = Container()

    def boom(_c):
        raise ValueError("bad config")

    c.bind(Store, boom)

    with pytest.raises(ValueError, match="bad config"):
        c.try_resolve(Store)


def test_cyclic_dependency_is_reported():
    c = Container()
    c.bind(Store, lambda c_: c_.resolve(Indexer))
    c.bind(Indexer, lambda c_: c_.resolve(Store))

    with pytest.raises(ContainerError, match="Cyclic dependency"):
        c.resolve(Store)


def test_string_tokens_resolve():
    """String tokens back services behind optional dependencies."""
    c = Container()
    c.bind("store", lambda _c: Store())

    assert isinstance(c.resolve("store"), Store)
    assert "store" in c


def test_rebinding_drops_the_cached_instance(container):
    first = container.resolve(Store)
    container.bind(Store, lambda _c: Store(url="postgres://other"))

    assert container.resolve(Store) is not first
    assert container.resolve(Store).url == "postgres://other"


def test_unbind_and_reset(container):
    first = container.resolve(Store)
    container.reset(Store)
    assert container.resolve(Store) is not first

    container.unbind(Store)
    assert not container.has(Store)


def test_async_factory_requires_aresolve():
    c = Container()

    async def build(_c):
        return Store()

    c.bind(Store, build)

    with pytest.raises(ContainerError, match="async factory"):
        c.resolve(Store)


# -- Substituting implementations (the test seam) -------------------------------------


def test_override_substitutes_a_fake_without_touching_production_code(container):
    """A fake integration is injected via the container; the graph is unchanged."""
    with container.override(Store, FakeStore()):
        indexer = container.resolve(Indexer)
        assert isinstance(indexer.store, FakeStore)
        assert indexer.store.url == "memory://fake"

    # The production binding is restored, and the overridden singleton is discarded.
    assert type(container.resolve(Indexer).store) is Store


def test_override_restores_a_previously_cached_instance(container):
    original = container.resolve(Store)

    with container.override(Store, FakeStore()):
        assert container.resolve(Store) is not original

    assert container.resolve(Store) is original


def test_override_factory(container):
    with container.override_factory(Store, lambda _c: Store(url="sqlite://tmp")):
        assert container.resolve(Store).url == "sqlite://tmp"

    assert container.resolve(Store).url == "postgres://prod"


def test_override_restores_on_exception(container):
    with pytest.raises(RuntimeError):
        with container.override(Store, FakeStore()):
            raise RuntimeError("boom")

    assert type(container.resolve(Store)) is Store


def test_override_of_unbound_token_is_removed_on_exit():
    c = Container()

    with c.override(Store, FakeStore()):
        assert isinstance(c.resolve(Store), FakeStore)

    assert not c.has(Store)


def test_override_reaches_dependents_built_before_the_block(container):
    """A singleton cached before the override still holds the real Store.

    Serving that cached instance inside the block would silently defeat the override,
    so entering clears the cache.
    """
    container.resolve(Indexer)

    with container.override(Store, FakeStore()):
        assert isinstance(container.resolve(Indexer).store, FakeStore)


def test_override_exit_discards_dependents_rebuilt_during_the_block(container):
    """Anything rebuilt while the fake was bound must not survive the block."""
    original = container.resolve(Indexer)

    with container.override(Store, FakeStore()):
        assert container.resolve(Indexer) is not original

    restored = container.resolve(Indexer)
    assert restored is original
    assert type(restored.store) is Store


# -- Child containers -----------------------------------------------------------------


def test_child_falls_back_to_parent(container):
    child = container.child()

    assert child.resolve(Store) is container.resolve(Store)
    assert child.has(Indexer)


def test_child_binding_does_not_leak_to_parent(container):
    child = container.child()
    child.bind_instance(Store, FakeStore())

    assert isinstance(child.resolve(Store), FakeStore)
    assert type(container.resolve(Store)) is Store


def test_child_is_the_resolution_context_for_inherited_bindings(container):
    """An inherited factory must see the child's bindings, or scoping is pointless.

    Without this, ``child.resolve(Indexer)`` would run the parent's factory against the
    parent and hand back an Indexer holding the production Store.
    """
    child = container.child()
    child.bind(Store, lambda _c: FakeStore())

    assert isinstance(child.resolve(Indexer).store, FakeStore)
    # The parent's own graph is untouched.
    assert type(container.resolve(Indexer).store) is Store


@pytest.mark.asyncio
async def test_child_is_the_resolution_context_for_inherited_bindings_async(container):
    child = container.child()
    child.bind(Store, lambda _c: FakeStore())

    assert isinstance((await child.aresolve(Indexer)).store, FakeStore)


def test_empty_child_shares_the_parent_singleton(container):
    """A scope that binds nothing cannot influence a factory, so it must not duplicate."""
    child = container.child()

    assert child.resolve(Indexer) is container.resolve(Indexer)


def test_has_local_ignores_the_parent(container):
    """A composition root needs 'is this mine?', not 'can this resolve?'.

    ``has`` answers the second, so a caller that used it to decide whether to install a
    default would skip it on any child and silently inherit the parent's.
    """
    child = container.child()

    assert child.has(Store) and not child.has_local(Store)
    assert container.has_local(Store)

    child.bind(Store, lambda _c: FakeStore())
    assert child.has_local(Store)


def test_child_tokens_include_parent_tokens_once(container):
    child = container.child()
    child.bind(Store, lambda _c: FakeStore())

    tokens = child.tokens()
    assert tokens.count(Store) == 1
    assert Indexer in tokens


# -- Async resolution -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_aresolve_supports_async_factories():
    c = Container()

    async def build_store(_c):
        await asyncio.sleep(0)
        return Store(url="async://store")

    c.bind(Store, build_store)
    c.bind(Indexer, lambda c_: Indexer(store=c_.resolve(Store)))

    store = await c.aresolve(Store)
    assert store.url == "async://store"
    # Once cached, a sync resolve of a dependent service sees the same instance.
    assert c.resolve(Indexer).store is store


@pytest.mark.asyncio
async def test_aresolve_handles_sync_factories(container):
    assert await container.aresolve(Indexer) is container.resolve(Indexer)


@pytest.mark.asyncio
async def test_concurrent_aresolve_builds_a_singleton_once():
    c = Container()
    builds = []

    async def build_store(_c):
        await asyncio.sleep(0.01)
        store = Store()
        builds.append(store)
        return store

    c.bind(Store, build_store)

    results = await asyncio.gather(*(c.aresolve(Store) for _ in range(5)))

    assert len(builds) == 1
    assert all(r is builds[0] for r in results)


@pytest.mark.asyncio
async def test_concurrent_transient_aresolve_is_not_serialized():
    """Transients share no instance, so the per-token lock must not apply to them."""
    c = Container()
    concurrent = []

    async def build_store(_c):
        concurrent.append(1)
        await asyncio.sleep(0.01)
        return Store()

    c.bind(Store, build_store, singleton=False)

    results = await asyncio.gather(*(c.aresolve(Store) for _ in range(3)))

    assert len(concurrent) == 3
    assert len({id(r) for r in results}) == 3


@pytest.mark.asyncio
async def test_cross_task_deadlock_is_reported_not_hung():
    """Two tasks each holding one token's lock while awaiting the other's would hang.

    Neither task re-enters a token it is itself building, so the per-task stacks cannot
    see it; the wait-for graph can.
    """
    c = Container()
    ready = asyncio.Event()

    async def build_store(c_):
        ready.set()
        return Indexer(store=await c_.aresolve(Indexer))

    async def build_indexer(c_):
        await ready.wait()
        return await c_.aresolve(Store)

    c.bind(Store, build_store)
    c.bind(Indexer, build_indexer)

    results = await asyncio.wait_for(
        asyncio.gather(c.aresolve(Store), c.aresolve(Indexer), return_exceptions=True),
        timeout=5,
    )

    assert any(isinstance(r, ContainerError) for r in results)


@pytest.mark.asyncio
async def test_atry_resolve(container):
    assert await container.atry_resolve(FakeStore) is None
    assert await container.atry_resolve(Store) is not None


@pytest.mark.asyncio
async def test_async_override(container):
    async with container.override(Store, FakeStore()):
        assert isinstance(await container.aresolve(Store), FakeStore)

    assert type(container.resolve(Store)) is Store
