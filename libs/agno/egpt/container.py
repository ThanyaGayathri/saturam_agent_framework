"""Lightweight dependency-injection container for INTERNAL service wiring.

The container is the composition root for internal services and integration bindings
(control-plane services, providers, stores, verifiers, schedulers). It exists so those
implementations are swappable and testable without editing production code.

It is deliberately **not** part of the public, ergonomic API: users keep writing
``Agent(model=..., tools=..., db=...)`` and never touch a container. See ``docs/di.md``
for the contributor guide (what belongs in the container vs the public API) and
``docs/adr/0002-dependency-injection.md`` for the rationale behind a custom container.

Example:
    from egpt.container import Container

    container = Container(name="my-services")
    container.bind(Store, lambda c: PostgresStore(url="..."))
    container.bind(Indexer, lambda c: Indexer(store=c.resolve(Store)))

    indexer = container.resolve(Indexer)  # typed as Indexer

    # In tests, substitute an implementation without touching production code:
    with container.override(Store, FakeStore()):
        assert isinstance(container.resolve(Indexer).store, FakeStore)
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar, Union, overload

from egpt.exceptions import AgnoError

T = TypeVar("T")

# A binding is keyed either by the service type itself (preferred: keeps ``resolve`` typed)
# or by a string constant (used when importing the type would pull an optional dependency).
Token = Union[Type[Any], str]

# Factories always receive the container, so a factory can resolve its own dependencies.
# A factory may be sync or async; async factories are only resolvable via ``aresolve``.
Factory = Callable[["Container"], Any]

_MISSING = object()

# How many times ``aresolve`` rebuilds a token whose binding was replaced while its factory
# was suspended. A retry only happens when something rebinds concurrently, so exhausting
# this many is a rebind loop -- a bug -- and spinning on it forever would be worse than a
# clear error.
_MAX_RESOLVE_ATTEMPTS = 8

__all__ = ["Container", "ContainerError", "Factory", "Token"]


class ContainerError(AgnoError):
    """Raised when a binding is missing, cyclic, or registered incorrectly."""


def _token_name(token: Token) -> str:
    """Human-readable name for a token, used in error messages."""
    if isinstance(token, str):
        return f"'{token}'"
    return getattr(token, "__qualname__", None) or getattr(token, "__name__", None) or str(token)


@dataclass
class _Binding:
    factory: Factory
    singleton: bool


class Container:
    """A small, typed service container.

    Bindings map a token (a type, or a string constant for services behind optional
    dependencies) to a factory that builds the service. Singleton bindings (the default)
    are constructed once per container; transient bindings are constructed per resolve.

    Containers may be nested: a child falls back to its parent for unknown tokens, which
    keeps request-scoped overrides from leaking into the application-wide graph.
    """

    def __init__(self, name: Optional[str] = None, parent: Optional["Container"] = None) -> None:
        self.name = name or "container"
        self._parent = parent
        self._bindings: Dict[Token, _Binding] = {}
        self._instances: Dict[Token, Any] = {}
        # Bumped by every operation that clears the instance cache wholesale (``reset``,
        # and the override swap/restore). A build already in flight when one of those runs
        # may embed a dependency the clear was meant to discard, so it must not publish.
        # Per-token invalidation needs no counter: ``_Binding`` objects are rebuilt by
        # ``bind``/``bind_instance``, so identity comparison already catches a rebind.
        self._cache_epoch = 0
        # Guards the binding/instance maps and the in-progress stack. Reentrant so a
        # factory can resolve its own dependencies on the same thread.
        self._lock = RLock()
        # Per-token async locks so two concurrent tasks cannot build the same singleton
        # twice, while nested async resolution of *different* tokens still proceeds.
        self._async_locks: Dict[Token, asyncio.Lock] = {}
        self._resolving: List[Token] = []
        # Async resolution stacks are per task: two tasks building the same token
        # concurrently is contention (handled by the locks above), not a cycle.
        self._async_resolving: Dict[int, List[Token]] = {}
        # Wait-for graph for async resolution: which task is building a token, and which
        # token a task is blocked on. A cycle across these is a deadlock, not contention.
        self._async_owners: Dict[Token, int] = {}
        self._async_waits: Dict[int, Token] = {}

    def __repr__(self) -> str:
        return f"Container(name={self.name!r}, bindings={len(self._bindings)})"

    def __contains__(self, token: Token) -> bool:
        return self.has(token)

    @property
    def parent(self) -> Optional["Container"]:
        """The container this one falls back to, if any."""
        return self._parent

    # -- Registration --------------------------------------------------------------

    def bind(self, token: Token, factory: Factory, singleton: bool = True) -> "Container":
        """Bind a token to a factory.

        Args:
            token: The service type (preferred) or a string constant.
            factory: Callable receiving this container and returning the service. It may
                be async, in which case the token must be resolved with ``aresolve``.
            singleton: If True (default) the instance is built once and cached.

        Returns:
            The container, so bindings can be chained.
        """
        if not callable(factory):
            raise ContainerError(
                f"Binding for {_token_name(token)} must be a callable factory, got {type(factory).__name__}. "
                "Use bind_instance() to bind an already-constructed object."
            )
        with self._lock:
            self._bindings[token] = _Binding(factory=factory, singleton=singleton)
            self._instances.pop(token, None)
        return self

    def bind_instance(self, token: Token, instance: Any) -> "Container":
        """Bind a token to an already-constructed instance."""
        with self._lock:
            self._bindings[token] = _Binding(factory=lambda _c: instance, singleton=True)
            self._instances[token] = instance
        return self

    def unbind(self, token: Token) -> None:
        """Remove a binding (and any cached instance) from this container."""
        with self._lock:
            self._bindings.pop(token, None)
            self._instances.pop(token, None)
            self._async_locks.pop(token, None)

    def has(self, token: Token) -> bool:
        """Whether this container (or a parent) can resolve the token."""
        with self._lock:
            if token in self._bindings:
                return True
        return self._parent is not None and self._parent.has(token)

    def has_local(self, token: Token) -> bool:
        """Whether *this* container binds the token itself, ignoring any parent.

        ``has`` walks the parent chain; this deliberately does not. A composition root
        needs the distinction: a binding inherited from a parent was registered for a
        different owner, so it must not suppress the default this container needs. Use
        ``has`` to ask "can this resolve?", ``has_local`` to ask "is this mine?".
        """
        with self._lock:
            return token in self._bindings

    def tokens(self) -> List[Token]:
        """Tokens bound on this container, parents included."""
        with self._lock:
            local: List[Token] = list(self._bindings.keys())
        if self._parent is None:
            return local
        inherited = [token for token in self._parent.tokens() if token not in local]
        return local + inherited

    def child(self, name: Optional[str] = None) -> "Container":
        """Create a child container that falls back to this one for unknown tokens.

        A child inherits *bindings*, not instances -- see ``resolve`` for what that means
        for singleton identity.
        """
        return Container(name=name or f"{self.name}-child", parent=self)

    def reset(self, token: Optional[Token] = None) -> None:
        """Drop cached singletons so they are rebuilt on the next resolve."""
        with self._lock:
            if token is None:
                self._instances.clear()
            else:
                self._instances.pop(token, None)
            self._cache_epoch += 1

    # -- Resolution ----------------------------------------------------------------

    @overload
    def resolve(self, token: Type[T]) -> T: ...

    @overload
    def resolve(self, token: str) -> Any: ...

    def resolve(self, token: Token) -> Any:
        """Resolve a service, constructing it (and its dependencies) if needed.

        On a child container, an inherited binding is built with the **child** as the
        resolution context, so the child's own bindings win inside the parent's factories
        (see ``child``). The consequence is that an inherited singleton is cached on the
        child, not shared from the ancestor: two children of one parent get two instances.
        Bind with ``bind_instance`` when a service must be the same object in every scope.

        Raises:
            ContainerError: If the token is unbound, its dependencies are cyclic, or its
                factory is async (use ``aresolve`` instead).
        """
        with self._lock:
            cached = self._instances.get(token, _MISSING)
            if cached is not _MISSING:
                return cached
            binding = self._bindings.get(token)
            if binding is None:
                if not self._bindings:
                    # A scope with no bindings of its own cannot influence any factory,
                    # so delegate and share the parent's instance rather than duplicating.
                    return self._resolve_from_parent(token)
                binding = self._inherited_binding(token)
            epoch = self._cache_epoch

            self._enter(token)
            try:
                value = binding.factory(self)
            finally:
                self._exit(token)

            if inspect.isawaitable(value):
                self._close_awaitable(value)
                raise ContainerError(
                    f"{_token_name(token)} is bound to an async factory in container '{self.name}'. "
                    "Resolve it with `await container.aresolve(...)`."
                )
            if binding.singleton and self._is_current(token, binding, epoch):
                # The factory ran under the container-wide lock, so no other thread could
                # rebind mid-build: the only way to get here is a factory that rebound (or
                # reset) its own token while constructing. Hand back what it built, but do
                # not cache it over the newer binding. Unlike ``aresolve`` this does not
                # retry -- re-running a factory that rebinds itself would never terminate.
                self._instances[token] = value
            return value

    @overload
    def try_resolve(self, token: Type[T]) -> Optional[T]: ...

    @overload
    def try_resolve(self, token: str) -> Optional[Any]: ...

    def try_resolve(self, token: Token) -> Optional[Any]:
        """Resolve a service, or return None when the token is unbound.

        Errors raised by a bound factory still propagate: an unbound token is a
        configuration choice, a failing factory is a bug.
        """
        if not self.has(token):
            return None
        return self.resolve(token)

    @overload
    async def aresolve(self, token: Type[T]) -> T: ...

    @overload
    async def aresolve(self, token: str) -> Any: ...

    async def aresolve(self, token: Token) -> Any:
        """Async variant of ``resolve`` that also supports async factories.

        A factory is suspended across awaits, so the binding it was chosen from can be
        replaced, removed or overridden before it returns. Such a value is discarded rather
        than cached over the newer binding, and the resolve is retried against whatever is
        bound now -- so the caller gets the current implementation, not a superseded one.
        """
        # Every exit path below must unwind the per-task bookkeeping: its keys are
        # ``id(task)``, which CPython reuses once the task is collected, so a leaked
        # entry does not merely grow the dict -- it can make a later, unrelated task
        # look like the owner of a token and report a cycle that does not exist.
        stack = self._async_stack()
        try:
            if token in stack:
                chain = " -> ".join(_token_name(t) for t in [*stack, token])
                raise ContainerError(f"Cyclic dependency detected while resolving: {chain}")

            # The binding is re-read on every attempt: a retry exists precisely because the
            # one read last time is no longer in force.
            for _attempt in range(_MAX_RESOLVE_ATTEMPTS):
                with self._lock:
                    cached = self._instances.get(token, _MISSING)
                    if cached is not _MISSING:
                        return cached
                    binding = self._bindings.get(token)
                    delegate = binding is None and not self._bindings
                    epoch = self._cache_epoch

                if delegate:
                    if self._parent is None:
                        raise ContainerError(
                            f"No binding registered for {_token_name(token)} in container '{self.name}'."
                        )
                    return await self._parent.aresolve(token)
                if binding is None:
                    binding = self._inherited_binding(token)

                # Transient bindings share no instance, so serializing them would only add
                # latency; only singletons need the per-token lock. Nothing is published
                # either, so a rebind mid-build cannot poison anything -- no retry needed.
                if not binding.singleton:
                    return await self._build_async(token, binding, stack)

                self._enter_async_wait(token)
                try:
                    async with self._async_lock(token):
                        self._claim_async_owner(token)
                        try:
                            with self._lock:
                                # Whoever lost the race for the lock finds the instance already
                                # built. Claiming ownership above happens first so the release is
                                # paired, and this return is inside the try for the same reason.
                                cached = self._instances.get(token, _MISSING)
                                if cached is not _MISSING:
                                    return cached
                            value = await self._build_async(token, binding, stack)
                        finally:
                            self._release_async_owner(token)

                        with self._lock:
                            # A sync resolve on another thread can slip in while we await, so the
                            # first instance cached wins and ours is discarded -- a token bound as
                            # a singleton must never hand out two objects.
                            cached = self._instances.get(token, _MISSING)
                            if cached is not _MISSING:
                                return cached
                            if self._is_current(token, binding, epoch):
                                self._instances[token] = value
                                return value
                    # Superseded. Leaving the ``async with`` first is what makes the retry
                    # able to take the per-token lock again instead of deadlocking on it.
                finally:
                    self._clear_async_wait()

            raise ContainerError(
                f"Gave up resolving {_token_name(token)} in container '{self.name}' after "
                f"{_MAX_RESOLVE_ATTEMPTS} attempts: its binding was replaced every time its factory ran."
            )
        finally:
            self._release_async_stack()

    @overload
    async def atry_resolve(self, token: Type[T]) -> Optional[T]: ...

    @overload
    async def atry_resolve(self, token: str) -> Optional[Any]: ...

    async def atry_resolve(self, token: Token) -> Optional[Any]:
        """Async variant of ``try_resolve``."""
        if not self.has(token):
            return None
        return await self.aresolve(token)

    # -- Test seams ----------------------------------------------------------------

    def override(self, token: Token, instance: Any) -> "_Override":
        """Temporarily bind a token to an instance (context manager).

        This is the seam tests use to substitute a fake integration without touching
        production code. Supports both ``with`` and ``async with``.

        On exit the previous binding is restored **and** every singleton constructed
        while the override was active is evicted, so a service that captured the fake
        cannot outlive the block.
        """
        return _Override(self, token, lambda _c: instance, singleton=True)

    def override_factory(self, token: Token, factory: Factory, singleton: bool = True) -> "_Override":
        """Temporarily bind a token to a factory (context manager). See ``override``."""
        if not callable(factory):
            raise ContainerError(f"Override for {_token_name(token)} must be a callable factory.")
        return _Override(self, token, factory, singleton=singleton)

    # -- Internals -----------------------------------------------------------------

    def _resolve_from_parent(self, token: Token) -> Any:
        if self._parent is None:
            raise ContainerError(f"No binding registered for {_token_name(token)} in container '{self.name}'.")
        return self._parent.resolve(token)

    def _ancestor_binding(self, token: Token) -> Optional[_Binding]:
        """Walk the parent chain for a binding, without consulting this container."""
        node = self._parent
        while node is not None:
            with node._lock:
                binding = node._bindings.get(token)
            if binding is not None:
                return binding
            node = node._parent
        return None

    def _inherited_binding(self, token: Token) -> _Binding:
        """Find a binding on an ancestor, to be built with this container as context."""
        binding = self._ancestor_binding(token)
        if binding is None:
            raise ContainerError(f"No binding registered for {_token_name(token)} in container '{self.name}'.")
        return binding

    def _current_binding(self, token: Token) -> Optional[_Binding]:
        """The binding this container would use for ``token`` right now, or None if unbound.

        Mirrors the lookup ``resolve``/``aresolve`` perform -- local binding first, then the
        ancestor chain -- but reports absence instead of raising, so it can be used to ask
        "is the binding I captured still the one in force?".
        """
        with self._lock:
            binding = self._bindings.get(token)
        return binding if binding is not None else self._ancestor_binding(token)

    def _is_current(self, token: Token, binding: _Binding, epoch: int) -> bool:
        """Whether a value built from ``binding`` at ``epoch`` may still be published.

        Two ways it may not be: the binding was replaced or removed while the factory ran
        (identity), or the whole instance cache was invalidated under it (epoch). Callers
        hold ``self._lock``, so the answer cannot go stale before they act on it.
        """
        return self._cache_epoch == epoch and self._current_binding(token) is binding

    def _enter(self, token: Token) -> None:
        self._check_cycle(token)
        self._resolving.append(token)

    def _exit(self, token: Token) -> None:
        if self._resolving and self._resolving[-1] == token:
            self._resolving.pop()
        elif token in self._resolving:
            self._resolving.remove(token)

    def _check_cycle(self, token: Token) -> None:
        if token in self._resolving:
            chain = " -> ".join(_token_name(t) for t in [*self._resolving, token])
            raise ContainerError(f"Cyclic dependency detected while resolving: {chain}")

    def _async_stack(self) -> List[Token]:
        """The resolution stack of the calling task (created on first use).

        Async resolution stacks are per task so that two tasks awaiting the same
        singleton register as contention (serialized by ``_async_lock``) rather than as a
        cycle. Only a task re-entering a token it is already building is a real cycle.
        """
        key = id(asyncio.current_task())
        with self._lock:
            stack = self._async_resolving.get(key)
            if stack is None:
                stack = []
                self._async_resolving[key] = stack
            return stack

    def _release_async_stack(self) -> None:
        """Drop the calling task's stack once it is empty, so finished tasks don't leak."""
        key = id(asyncio.current_task())
        with self._lock:
            stack = self._async_resolving.get(key)
            if stack is not None and not stack:
                self._async_resolving.pop(key, None)

    def _async_lock(self, token: Token) -> asyncio.Lock:
        with self._lock:
            lock = self._async_locks.get(token)
            if lock is None:
                lock = asyncio.Lock()
                self._async_locks[token] = lock
            return lock

    async def _build_async(self, token: Token, binding: _Binding, stack: List[Token]) -> Any:
        """Run a factory with the calling task's resolution stack marked for cycle detection.

        Dropping the stack once it empties is ``aresolve``'s job -- it owns every path that
        acquires one, including the ones that never reach this method.
        """
        stack.append(token)
        try:
            value = binding.factory(self)
            if inspect.isawaitable(value):
                value = await value
            return value
        finally:
            stack.remove(token)

    def _enter_async_wait(self, token: Token) -> None:
        """Record that this task is about to wait for ``token``, refusing to deadlock.

        Two tasks can each hold the lock for one token while waiting on the other's; the
        per-task stacks cannot see that, because neither task is re-entering a token it is
        itself building. Walking the wait-for graph catches it and raises instead of
        hanging forever.
        """
        key = id(asyncio.current_task())
        with self._lock:
            chain: List[Token] = [token]
            probe: Optional[Token] = token
            seen = set()
            while probe is not None and probe not in seen:
                seen.add(probe)
                owner = self._async_owners.get(probe)
                if owner is None:
                    break
                if owner == key:
                    names = " -> ".join(_token_name(t) for t in chain)
                    raise ContainerError(f"Cyclic dependency detected across concurrent tasks: {names}")
                probe = self._async_waits.get(owner)
                if probe is not None:
                    chain.append(probe)
            self._async_waits[key] = token

    def _claim_async_owner(self, token: Token) -> None:
        """Mark this task as the builder of ``token``; it is no longer waiting."""
        key = id(asyncio.current_task())
        with self._lock:
            self._async_owners[token] = key
            self._async_waits.pop(key, None)

    def _release_async_owner(self, token: Token) -> None:
        key = id(asyncio.current_task())
        with self._lock:
            if self._async_owners.get(token) == key:
                self._async_owners.pop(token, None)

    def _clear_async_wait(self) -> None:
        with self._lock:
            self._async_waits.pop(id(asyncio.current_task()), None)

    @staticmethod
    def _close_awaitable(value: Any) -> None:
        """Close a coroutine we are not going to await, to avoid a RuntimeWarning."""
        close = getattr(value, "close", None)
        if callable(close):
            close()

    def _swap(self, token: Token, binding: _Binding) -> Tuple[Optional[_Binding], Dict[Token, Any]]:
        """Install an override binding and empty the singleton cache.

        The whole cache goes, not just the overridden token: a singleton built *before*
        the block already holds the real implementation, and leaving it cached would hand
        that back to anything resolved inside the block. The snapshot is restored verbatim
        on exit, so pre-existing instances survive the round trip by identity.
        """
        with self._lock:
            previous_binding = self._bindings.get(token)
            snapshot = dict(self._instances)
            self._instances.clear()
            self._bindings[token] = binding
            self._cache_epoch += 1
            return previous_binding, snapshot

    def _restore(
        self,
        token: Token,
        previous_binding: Optional[_Binding],
        snapshot: Dict[Token, Any],
    ) -> None:
        with self._lock:
            if previous_binding is None:
                self._bindings.pop(token, None)
            else:
                self._bindings[token] = previous_binding
            # Everything built during the block is discarded -- any of it may hold the
            # substituted implementation -- and the pre-block cache is put back exactly.
            self._instances.clear()
            self._instances.update(snapshot)
            self._cache_epoch += 1


class _Override:
    """Context manager returned by ``Container.override`` / ``override_factory``."""

    def __init__(self, container: Container, token: Token, factory: Factory, singleton: bool) -> None:
        self._container = container
        self._token = token
        self._binding = _Binding(factory=factory, singleton=singleton)
        self._previous_binding: Optional[_Binding] = None
        self._snapshot: Dict[Token, Any] = {}

    def __enter__(self) -> Container:
        self._previous_binding, self._snapshot = self._container._swap(self._token, self._binding)
        return self._container

    def __exit__(self, *exc_info: Any) -> None:
        self._container._restore(self._token, self._previous_binding, self._snapshot)

    async def __aenter__(self) -> Container:
        return self.__enter__()

    async def __aexit__(self, *exc_info: Any) -> None:
        self.__exit__(*exc_info)
