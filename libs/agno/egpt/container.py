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

    def __repr__(self) -> str:
        return f"Container(name={self.name!r}, bindings={len(self._bindings)})"

    def __contains__(self, token: Token) -> bool:
        return self.has(token)

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

    def tokens(self) -> List[Token]:
        """Tokens bound on this container, parents included."""
        with self._lock:
            local: List[Token] = list(self._bindings.keys())
        if self._parent is None:
            return local
        inherited = [token for token in self._parent.tokens() if token not in local]
        return local + inherited

    def child(self, name: Optional[str] = None) -> "Container":
        """Create a child container that falls back to this one for unknown tokens."""
        return Container(name=name or f"{self.name}-child", parent=self)

    def reset(self, token: Optional[Token] = None) -> None:
        """Drop cached singletons so they are rebuilt on the next resolve."""
        with self._lock:
            if token is None:
                self._instances.clear()
            else:
                self._instances.pop(token, None)

    # -- Resolution ----------------------------------------------------------------

    @overload
    def resolve(self, token: Type[T]) -> T: ...

    @overload
    def resolve(self, token: str) -> Any: ...

    def resolve(self, token: Token) -> Any:
        """Resolve a service, constructing it (and its dependencies) if needed.

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
                return self._resolve_from_parent(token)

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
            if binding.singleton:
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
        """Async variant of ``resolve`` that also supports async factories."""
        with self._lock:
            cached = self._instances.get(token, _MISSING)
            if cached is not _MISSING:
                return cached
            binding = self._bindings.get(token)
        if binding is None:
            if self._parent is None:
                raise ContainerError(f"No binding registered for {_token_name(token)} in container '{self.name}'.")
            return await self._parent.aresolve(token)

        stack = self._async_stack()
        if token in stack:
            chain = " -> ".join(_token_name(t) for t in [*stack, token])
            raise ContainerError(f"Cyclic dependency detected while resolving: {chain}")

        async with self._async_lock(token):
            with self._lock:
                cached = self._instances.get(token, _MISSING)
                if cached is not _MISSING:
                    return cached
            stack.append(token)
            try:
                value = binding.factory(self)
                if inspect.isawaitable(value):
                    value = await value
            finally:
                stack.remove(token)
                self._release_async_stack()

            with self._lock:
                if binding.singleton:
                    self._instances[token] = value
            return value

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

    @staticmethod
    def _close_awaitable(value: Any) -> None:
        """Close a coroutine we are not going to await, to avoid a RuntimeWarning."""
        close = getattr(value, "close", None)
        if callable(close):
            close()

    def _swap(self, token: Token, binding: _Binding) -> Tuple[Optional[_Binding], Any, List[Token]]:
        with self._lock:
            previous_binding = self._bindings.get(token)
            previous_instance = self._instances.pop(token, _MISSING)
            cached_before = list(self._instances.keys())
            self._bindings[token] = binding
            return previous_binding, previous_instance, cached_before

    def _restore(
        self,
        token: Token,
        previous_binding: Optional[_Binding],
        previous_instance: Any,
        cached_before: List[Token],
    ) -> None:
        with self._lock:
            if previous_binding is None:
                self._bindings.pop(token, None)
            else:
                self._bindings[token] = previous_binding
            # Evict anything built while the override was active: those instances may
            # hold the substituted implementation.
            for cached_token in list(self._instances.keys()):
                if cached_token not in cached_before:
                    self._instances.pop(cached_token, None)
            self._instances.pop(token, None)
            if previous_instance is not _MISSING:
                self._instances[token] = previous_instance


class _Override:
    """Context manager returned by ``Container.override`` / ``override_factory``."""

    def __init__(self, container: Container, token: Token, factory: Factory, singleton: bool) -> None:
        self._container = container
        self._token = token
        self._binding = _Binding(factory=factory, singleton=singleton)
        self._previous_binding: Optional[_Binding] = None
        self._previous_instance: Any = _MISSING
        self._cached_before: List[Token] = []

    def __enter__(self) -> Container:
        self._previous_binding, self._previous_instance, self._cached_before = self._container._swap(
            self._token, self._binding
        )
        return self._container

    def __exit__(self, *exc_info: Any) -> None:
        self._container._restore(self._token, self._previous_binding, self._previous_instance, self._cached_before)

    async def __aenter__(self) -> Container:
        return self.__enter__()

    async def __aexit__(self, *exc_info: Any) -> None:
        self.__exit__(*exc_info)
